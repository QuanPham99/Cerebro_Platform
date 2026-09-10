from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from . import generation as generation_service
from .bundle import load_validated_bundle
from .bundle_versions import (
    BundleDefaultLocked,
    BundleVersionInvalid,
    BundleVersionNotFound,
    BundleVersionRegistry,
)
from .chat import ChatOrchestrator
from .definitions import (
    DefinitionConflictError,
    DefinitionProviderUnavailable,
    DefinitionRevisionManager,
    DefinitionRevisionNotFound,
    DefinitionValidationError,
)
from .enrichment import provider_from_environment
from .generation import ActivationError, ReviewConflictError, ReviewValidationError
from .generation_runs import GenerationRunConflict, GenerationRunManager, GenerationRunNotFound, GenerationRunNotReady
from .models import (
    AgentTrace,
    BundleDefaultRequest,
    ChatRequest,
    ChatResponse,
    DefinitionApplyRequest,
    DefinitionRevisionCreateRequest,
    DefinitionTranslateRequest,
    GenerationStartRequest,
    GroundingResponse,
    ReviewRequest,
    SemanticObject,
)
from .paths import DEFAULT_BUNDLE, DEFAULT_CONFIG, ROOT
from .retrieval import SemanticRetriever, embedder_from_environment
from .settings import Settings, resolve_active_bundle
from .source import DuckDBSource


def create_mcp_server(
    retriever: SemanticRetriever | Callable[[], SemanticRetriever],
) -> FastMCP:
    get_retriever = retriever if callable(retriever) else lambda: retriever
    server = FastMCP(
        "Cerebro Semantic Grounding",
        instructions="Retrieve reviewed OKF semantics for downstream text-to-SQL grounding. This server never executes SQL.",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )

    @server.tool()
    def retrieve_grounding(question: str, limit: int = 10) -> dict:
        """Retrieve typed semantics, physical bindings, safe joins, warnings, and provenance."""
        if not question.strip():
            raise ValueError("question must not be empty")
        current = get_retriever()
        return current.grounding(question, min(max(limit, 1), 50)).model_dump(mode="json")

    @server.tool()
    def get_concept(concept_id: str) -> dict:
        """Return one semantic object by its stable OKF identifier."""
        obj = get_retriever().by_id.get(concept_id)
        if obj is None:
            raise ValueError(f"Unknown concept ID: {concept_id}")
        return obj.model_dump(mode="json")

    @server.tool()
    def expand_neighborhood(concept_ids: list[str], depth: int = 1) -> dict:
        """Expand stable identifiers over typed semantic graph edges."""
        if depth < 0 or depth > 3:
            raise ValueError("depth must be between 0 and 3")
        current = get_retriever()
        unknown = sorted(set(concept_ids) - set(current.by_id))
        if unknown:
            raise ValueError(f"Unknown concept IDs: {', '.join(unknown)}")
        ids = current.expand(concept_ids, depth)
        return {"semantic_version": current.bundle.version, "depth": depth, "concept_ids": ids}

    return server


def create_app(
    bundle_path: Path | str | None = None,
    source_config: Path | str = DEFAULT_CONFIG,
    generation_output_root: Path | str | None = None,
    reviewed_output_root: Path | str | None = None,
    web_dist_path: Path | str | None = None,
) -> FastAPI:
    settings = Settings.from_environment()
    resolved_bundle_path = resolve_active_bundle(bundle_path)
    bundle = load_validated_bundle(resolved_bundle_path)
    retriever = SemanticRetriever(bundle, embedder=embedder_from_environment())
    golden_bundle = load_validated_bundle(DEFAULT_BUNDLE)
    golden_retriever = SemanticRetriever(golden_bundle)
    provider = provider_from_environment()
    try:
        source: DuckDBSource | None = DuckDBSource(source_config, settings.database_path)
    except FileNotFoundError:
        source = None
    chat_orchestrator = (
        ChatOrchestrator(bundle, retriever, source.database_path, settings, provider)
        if source is not None
        else None
    )
    runtime_database_path = source.database_path if source is not None else settings.database_path
    runtime = {"bundle": bundle, "retriever": retriever, "chat": chat_orchestrator}

    def prepare_runtime(next_path: Path) -> dict:
        next_bundle = load_validated_bundle(next_path)
        next_retriever = SemanticRetriever(next_bundle, embedder=embedder_from_environment())
        next_chat = (
            ChatOrchestrator(next_bundle, next_retriever, runtime_database_path, settings, provider_from_environment())
            if runtime_database_path is not None
            else None
        )
        return {"bundle": next_bundle, "retriever": next_retriever, "chat": next_chat}

    def install_runtime(next_runtime: dict) -> None:
        runtime.update(next_runtime)
        app.state.bundle = next_runtime["bundle"]
        app.state.retriever = next_runtime["retriever"]
        app.state.chat = next_runtime["chat"]

    def swap_runtime(reviewed_path: Path) -> None:
        install_runtime(prepare_runtime(reviewed_path))

    reviewed_root = Path(reviewed_output_root) if reviewed_output_root is not None else ROOT / "knowledge" / "reviewed"
    default_change_allowed = bundle_path is None and not bool(os.getenv("CEREBRO_BUNDLE_PATH", "").strip())
    version_registry = BundleVersionRegistry(
        golden_root=DEFAULT_BUNDLE,
        reviewed_root=reviewed_root,
        active_pointer=generation_service.ACTIVE_BUNDLE_POINTER,
        default_change_allowed=default_change_allowed,
    )

    generation_manager = GenerationRunManager(
        database_path=runtime_database_path,
        provider_factory=provider_from_environment,
        config_path=source_config,
        database_schema=settings.database_schema,
        on_activate=swap_runtime,
        **({"output_root": generation_output_root} if generation_output_root is not None else {}),
        **({"reviewed_root": reviewed_output_root} if reviewed_output_root is not None else {}),
    )
    definition_manager = DefinitionRevisionManager(
        active_bundle=lambda: runtime["bundle"],
        provider_factory=provider_from_environment,
        output_root=Path(generation_output_root) if generation_output_root is not None else ROOT / "knowledge" / "generated",
        reviewed_root=reviewed_root,
        resolve_bundle=lambda identifier: version_registry.resolve(identifier).bundle,
        active_bundle_id=lambda: version_registry.catalog(runtime["bundle"].root).default_id,
        on_activate=swap_runtime,
    )
    mcp_server = create_mcp_server(lambda: runtime["retriever"])
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(
        title="Cerebro Semantic Layer",
        version=bundle.version,
        description=(
            "Explainable OKF grounding API with optional governed, read-only database chat."
        ),
        lifespan=lifespan,
    )
    app.state.bundle = bundle
    app.state.retriever = retriever
    app.state.chat = chat_orchestrator
    app.state.provider = provider
    app.state.generation = generation_manager
    app.state.bundle_versions = version_registry
    if settings.basic_auth_enabled:
        # Off by default. Every serving path (including the mounted MCP app and the
        # static SPA) previously relied entirely on an external Caddy sidecar for
        # auth (see deploy/Caddyfile) — that sidecar isn't part of the GreenNode
        # Agent Runtime target, so this is a fallback gate to avoid ever shipping a
        # fully open deployment. Prefer a platform-native gateway/access-control
        # layer over this when one is confirmed available.
        expected_user = settings.basic_auth_user or ""
        expected_password = settings.basic_auth_password or ""
        # Both the image's own HEALTHCHECK and any platform-level liveness/readiness
        # probe (Docker, GreenNode Agent Runtime) hit these unauthenticated — they
        # carry no secrets (already-redacted status only), so they stay open even
        # when Basic Auth is enabled for every other route.
        unauthenticated_paths = {"/health", "/api/health", "/api/health/ready"}

        @app.middleware("http")
        async def enforce_basic_auth(request: Request, call_next):
            if request.url.path in unauthenticated_paths:
                return await call_next(request)
            scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
            supplied_user = supplied_password = ""
            if scheme.lower() == "basic" and encoded:
                try:
                    decoded = base64.b64decode(encoded).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    decoded = ""
                supplied_user, _, supplied_password = decoded.partition(":")
            authorized = hmac.compare_digest(supplied_user, expected_user) and hmac.compare_digest(
                supplied_password, expected_password
            )
            if not authorized:
                return Response(
                    status_code=401,
                    content="Unauthorized",
                    headers={"WWW-Authenticate": 'Basic realm="Cerebro"'},
                )
            return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["*"],
    )

    web_dist = Path(web_dist_path) if web_dist_path is not None else ROOT / "apps" / "web" / "dist"

    @app.get("/health")
    async def platform_health() -> dict:
        # GreenNode Agent Runtime hardcodes its liveness probe to GET /health — this is
        # deliberately dependency-free (never checks the bundle/database/LLM) so a transient
        # backend issue can't make the platform kill and restart an otherwise-healthy process.
        # Use /api/health/ready for a real readiness contract.
        return {"status": "ok"}

    @app.get("/api/health")
    async def health() -> dict:
        from .agent_api import agent_status

        current = runtime["bundle"]
        return {
            "status": "ok",
            "bundle": current.name,
            "version": current.version,
            "objects": len(current.objects),
            "web_ui": "built" if (web_dist / "index.html").is_file() else "not_built",
            "agent": agent_status(getattr(app.state, "agent_runtime", None)),
        }

    def checked_bundle(path: Path | str) -> dict[str, str]:
        try:
            checked = load_validated_bundle(path)
            if checked.review_state != "approved":
                raise ValueError("bundle is not approved")
            return {"status": "ok", "name": checked.name, "version": checked.version}
        except Exception:  # The readiness contract intentionally redacts parser and path details.
            return {"status": "error"}

    def database_component() -> dict[str, object]:
        configured = runtime_database_path is not None
        if not configured:
            return {"status": "error", "configured": False}
        try:
            with duckdb.connect(str(runtime_database_path), read_only=True) as connection:
                reachable = connection.execute("SELECT 1").fetchone() == (1,)
        except Exception:  # Driver messages can contain filesystem paths; never return them here.
            reachable = False
        return {"status": "ok" if reachable else "error", "configured": True}

    @app.get("/api/health/ready")
    async def readiness() -> Response:
        components: dict[str, dict[str, object]] = {
            "active_bundle": checked_bundle(runtime["bundle"].root),
            "golden_bundle": checked_bundle(DEFAULT_BUNDLE),
            "web_ui": {"status": "ok" if (web_dist / "index.html").is_file() else "error"},
            "database": database_component(),
            "llm": {
                "status": "ok" if settings.llm_configured else "error",
                "configured": settings.llm_configured,
            },
        }
        ready = all(component["status"] == "ok" for component in components.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "components": components},
        )

    @app.get("/api/runtime/status")
    async def runtime_status() -> dict:
        current = runtime["bundle"]
        status = settings.public_status()
        status.update(
            {
                "database_configured": runtime_database_path is not None,
                "database_reachable": bool(runtime_database_path and runtime_database_path.is_file()),
                "web_ui": "built" if (web_dist / "index.html").is_file() else "not_built",
                "bundle": current.name,
                "semantic_version": current.version,
                "generation_mode": current.generation_mode,
                "review_state": current.review_state,
                "chat_ready": bool(settings.llm_configured and runtime_database_path and runtime_database_path.is_file()),
                "resolved_response_mode": getattr(provider, "resolved_response_mode", None),
            }
        )
        return status

    def bundle_info(current) -> dict:
        counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        for obj in current.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
            kind_counts[obj.profile_kind] = kind_counts.get(obj.profile_kind, 0) + 1
        return {
            "name": current.name,
            "version": current.version,
            "root": current.root,
            "counts": counts,
            "kind_counts": kind_counts,
            "okf_version": current.okf_version,
            "semantic_profile_version": current.semantic_profile_version,
            "generation_mode": current.generation_mode,
            "review_state": current.review_state,
            "provider": current.provider,
            "model": current.model,
            "source_mode": current.source_mode,
            "discovery_evidence": current.discovery_evidence,
        }

    @app.get("/api/bundles/active")
    async def active_bundle() -> dict:
        return bundle_info(runtime["bundle"])

    @app.get("/api/bundles/golden")
    async def golden_bundle_info() -> dict:
        return bundle_info(golden_bundle)

    @app.get("/api/bundles")
    async def bundle_versions() -> dict:
        return version_registry.catalog(runtime["bundle"].root).model_dump(mode="json")

    @app.get("/api/bundles/{bundle_id}/graph")
    async def bundle_version_graph(bundle_id: str) -> dict:
        try:
            return version_registry.graph(bundle_id)
        except BundleVersionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_bundle_version"}) from exc
        except BundleVersionInvalid as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "bundle_version_invalid", "message": str(exc)},
            ) from exc

    @app.get("/api/bundles/{bundle_id}/objects/{object_id}")
    async def bundle_version_object(bundle_id: str, object_id: str) -> SemanticObject:
        try:
            obj = version_registry.object(bundle_id, object_id)
        except BundleVersionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_bundle_version"}) from exc
        except BundleVersionInvalid as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "bundle_version_invalid", "message": str(exc)},
            ) from exc
        if obj is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_object", "id": object_id})
        return obj

    @app.put("/api/bundles/default")
    async def set_default_bundle(payload: BundleDefaultRequest) -> dict:
        try:
            resolved = version_registry.resolve(payload.bundle_id)
            prepared = prepare_runtime(resolved.path)
            _, activation = version_registry.set_default(payload.bundle_id)
            install_runtime(prepared)
            return activation
        except BundleVersionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_bundle_version"}) from exc
        except BundleDefaultLocked as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "default_locked_by_configuration", "message": str(exc)},
            ) from exc
        except (BundleVersionInvalid, ActivationError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "activation_blocked", "message": str(exc)},
            ) from exc

    @app.get("/api/golden/graph")
    async def golden_graph() -> dict:
        return golden_retriever.graph().model_dump(mode="json")

    @app.get("/api/golden/objects/{object_id}")
    async def golden_object(object_id: str) -> SemanticObject:
        obj = golden_retriever.by_id.get(object_id)
        if obj is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_object", "id": object_id})
        return obj

    @app.get("/api/definitions/context")
    async def definition_context(
        base_bundle_id: str | None = Query(default=None),
        revision_id: str | None = Query(default=None),
    ) -> dict:
        try:
            return definition_manager.context(
                base_bundle_id=base_bundle_id,
                revision_id=revision_id,
            )
        except BundleVersionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_bundle_version"}) from exc
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc
        except (BundleVersionInvalid, DefinitionValidationError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "definition_context_unavailable", "message": str(exc)},
            ) from exc

    @app.get("/api/graph")
    async def graph() -> dict:
        return runtime["retriever"].graph().model_dump(mode="json")

    @app.get("/api/concepts/{concept_id}")
    async def get_concept(concept_id: str) -> SemanticObject:
        obj = runtime["retriever"].by_id.get(concept_id)
        if obj is None:
            raise HTTPException(
                status_code=404, detail={"code": "unknown_concept", "id": concept_id}
            )
        return obj

    @app.get("/api/search")
    async def search(
        q: str = Query(min_length=1),
        types: str | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict:
        requested = (
            {item.strip() for item in types.split(",") if item.strip()}
            if types
            else None
        )
        if requested:
            allowed = {
                "dataset", "table", "physical_table", "concept", "legacy_concept",
                "entity", "dimension", "metric", "business_rule", "relationship", "policy",
            }
            unknown = requested - allowed
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "unknown_types", "types": sorted(unknown)},
                )
        return {
            "query": q,
            "retrieval_mode": "lexical_graph",
            "results": [item.model_dump(mode="json") for item in runtime["retriever"].search(q, limit, requested)],
        }

    @app.post("/api/grounding", response_model=GroundingResponse)
    async def grounding(payload: dict) -> GroundingResponse:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=422, detail={"code": "empty_question"})
        return runtime["retriever"].grounding(question, int(payload.get("limit", 10)))

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        current_chat = runtime["chat"]
        current_bundle = runtime["bundle"]
        if current_chat is None:
            return ChatResponse(
                conversation_id=payload.conversation_id or "unavailable",
                status="blocked",
                answer="Configure CEREBRO_DATABASE_PATH with a readable DuckDB database, then restart the server.",
                semantic_version=current_bundle.version,
                trace=[AgentTrace(agent="orchestrator", status="blocked", summary="Database is not configured")],
            )
        return current_chat.chat(payload)

    @app.post("/api/generation/runs", status_code=202)
    async def start_generation(payload: GenerationStartRequest | None = None) -> dict:
        source_mode = payload.source_mode if payload else "configured"
        if source_mode == "configured" and source is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "database_unavailable", "message": "Configure a readable DuckDB source, then restart the server."},
            )
        if source_mode == "database_only" and runtime_database_path is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "database_path_not_configured", "message": "Database-only mode requires a readable DuckDB path in server configuration or CEREBRO_DATABASE_PATH."},
            )
        try:
            return generation_manager.start(source_mode).model_dump(mode="json")
        except GenerationRunConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "generation_in_progress", "run_id": exc.run_id},
            ) from exc

    @app.get("/api/generation/runs/{run_id}")
    async def get_generation(run_id: str) -> dict:
        try:
            return generation_manager.get(run_id).model_dump(mode="json")
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc

    @app.get("/api/generation/runs/{run_id}/events")
    async def generation_events(run_id: str, request: Request) -> StreamingResponse:
        try:
            generation_manager.get(run_id)
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        try:
            cursor = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = 0

        async def stream():
            nonlocal cursor
            while True:
                events, terminal = generation_manager.events_after(run_id, cursor)
                for event in events:
                    cursor = event.sequence
                    payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
                    yield f"id: {event.sequence}\nevent: progress\ndata: {payload}\n\n"
                if terminal:
                    run_payload = json.dumps(
                        generation_manager.get(run_id).model_dump(mode="json"), separators=(",", ":")
                    )
                    yield f"event: complete\ndata: {run_payload}\n\n"
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.15)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/generation/runs/{run_id}/trace")
    async def generation_trace(run_id: str) -> dict:
        try:
            return generation_manager.trace(run_id).model_dump(mode="json")
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc

    @app.get("/api/generation/runs/{run_id}/graph")
    async def generation_graph(run_id: str) -> dict:
        try:
            return generation_manager.graph(run_id)
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        except GenerationRunNotReady as exc:
            raise HTTPException(status_code=409, detail={"code": "candidate_not_ready"}) from exc

    @app.get("/api/generation/runs/{run_id}/concepts/{concept_id}")
    async def generation_concept(run_id: str, concept_id: str) -> SemanticObject:
        try:
            obj = generation_manager.concept(run_id, concept_id)
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        except GenerationRunNotReady as exc:
            raise HTTPException(status_code=409, detail={"code": "candidate_not_ready"}) from exc
        if obj is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_concept", "id": concept_id})
        return obj

    @app.get("/api/generation/runs/{run_id}/snapshot")
    async def generation_snapshot(run_id: str) -> dict:
        try:
            return generation_manager.snapshot(run_id)
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        except GenerationRunNotReady as exc:
            raise HTTPException(status_code=409, detail={"code": "candidate_not_ready"}) from exc

    @app.get("/api/generation/runs/{run_id}/documents/{document_path:path}")
    async def generation_document(run_id: str, document_path: str) -> Response:
        try:
            path = generation_manager.document(run_id, document_path)
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        except GenerationRunNotReady as exc:
            raise HTTPException(status_code=409, detail={"code": "candidate_not_ready"}) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_document"}) from exc
        return Response(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.post("/api/generation/runs/{run_id}/reviews")
    async def review_generation(run_id: str, payload: ReviewRequest) -> dict:
        try:
            return generation_manager.review(run_id, payload).model_dump(mode="json")
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        except GenerationRunNotReady as exc:
            raise HTTPException(status_code=409, detail={"code": "candidate_not_ready"}) from exc
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "review_conflict", "message": str(exc)}) from exc
        except ReviewValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_review", "message": str(exc)}) from exc

    @app.post("/api/generation/runs/{run_id}/activate")
    async def activate_generation(run_id: str) -> dict:
        try:
            return generation_manager.activate(run_id)
        except GenerationRunNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_generation_run"}) from exc
        except GenerationRunNotReady as exc:
            raise HTTPException(status_code=409, detail={"code": "candidate_not_ready"}) from exc
        except ActivationError as exc:
            raise HTTPException(status_code=409, detail={"code": "activation_blocked", "message": str(exc)}) from exc

    @app.post("/api/definitions/translate")
    async def translate_definition(payload: DefinitionTranslateRequest) -> dict:
        try:
            return definition_manager.translate(payload).model_dump(mode="json")
        except BundleVersionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_bundle_version"}) from exc
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc
        except BundleVersionInvalid as exc:
            raise HTTPException(status_code=409, detail={"code": "bundle_version_invalid", "message": str(exc)}) from exc
        except DefinitionProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail={"code": "definition_provider_unavailable", "message": str(exc)}) from exc
        except (DefinitionValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_definition", "message": str(exc)}) from exc

    @app.post("/api/definition-revisions", status_code=201)
    async def create_definition_revision(payload: DefinitionRevisionCreateRequest) -> dict:
        try:
            return definition_manager.create(payload).model_dump(mode="json")
        except BundleVersionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_bundle_version"}) from exc
        except BundleVersionInvalid as exc:
            raise HTTPException(status_code=409, detail={"code": "bundle_version_invalid", "message": str(exc)}) from exc
        except DefinitionConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "definition_conflict", "message": str(exc)}) from exc
        except DefinitionValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_definition", "message": str(exc)}) from exc

    @app.get("/api/definition-revisions/{revision_id}")
    async def get_definition_revision(revision_id: str) -> dict:
        try:
            return definition_manager.get(revision_id).model_dump(mode="json")
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc

    @app.post("/api/definition-revisions/{revision_id}/definitions")
    async def add_definition(revision_id: str, payload: DefinitionApplyRequest) -> dict:
        try:
            return definition_manager.add(revision_id, payload).model_dump(mode="json")
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc
        except DefinitionConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "definition_conflict", "message": str(exc)}) from exc
        except DefinitionValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_definition", "message": str(exc)}) from exc

    @app.get("/api/definition-revisions/{revision_id}/graph")
    async def definition_revision_graph(revision_id: str) -> dict:
        try:
            return definition_manager.graph(revision_id)
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc

    @app.get("/api/definition-revisions/{revision_id}/objects/{object_id}")
    async def definition_revision_object(revision_id: str, object_id: str) -> SemanticObject:
        try:
            obj = definition_manager.object(revision_id, object_id)
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc
        if obj is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_object", "id": object_id})
        return obj

    @app.post("/api/definition-revisions/{revision_id}/reviews")
    async def review_definition_revision(revision_id: str, payload: ReviewRequest) -> dict:
        try:
            return definition_manager.review(revision_id, payload).model_dump(mode="json")
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "review_conflict", "message": str(exc)}) from exc
        except ReviewValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_review", "message": str(exc)}) from exc

    @app.post("/api/definition-revisions/{revision_id}/activate")
    async def activate_definition_revision(revision_id: str) -> dict:
        try:
            return definition_manager.activate(revision_id)
        except DefinitionRevisionNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_definition_revision"}) from exc
        except ActivationError as exc:
            raise HTTPException(status_code=409, detail={"code": "activation_blocked", "message": str(exc)}) from exc

    @app.get("/knowledge/{document_path:path}")
    async def knowledge_document(document_path: str) -> Response:
        bundle_root = Path(runtime["bundle"].root).resolve()
        requested = (bundle_root / document_path).resolve()
        if (
            bundle_root not in requested.parents
            or not requested.is_file()
            or requested.suffix != ".md"
        ):
            raise HTTPException(status_code=404, detail={"code": "unknown_document"})
        return Response(requested.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.api_route("/api/{unknown_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def unknown_api_route(unknown_path: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "unknown_api_route", "path": f"/api/{unknown_path}"},
        )

    app.mount("/mcp", mcp_app)
    if web_dist.exists():
        @app.get("/", include_in_schema=False)
        async def built_web_ui() -> Response:
            return Response(
                (web_dist / "index.html").read_text(encoding="utf-8"),
                media_type="text/html; charset=utf-8",
            )

        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    else:
        # An unbuilt UI used to answer a bare 404, which reads like a broken
        # server. Say what is missing and exactly how to supply it.
        @app.get("/")
        async def web_ui_not_built() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "web_ui_not_built",
                    "message": "The API and MCP server are running; the web UI bundle is absent.",
                    "build_command": "cd apps/web && npm install && npm run build",
                    "api_docs": "/docs",
                    "health": "/api/health",
                },
            )

    return app


app = create_app()
