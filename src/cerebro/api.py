from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from .bundle import load_validated_bundle
from .chat import ChatOrchestrator
from .enrichment import provider_from_environment
from .generation import ActivationError, ReviewConflictError, ReviewValidationError
from .generation_runs import GenerationRunConflict, GenerationRunManager, GenerationRunNotFound, GenerationRunNotReady
from .models import (
    AgentTrace,
    ChatRequest,
    ChatResponse,
    GenerationStartRequest,
    GroundingResponse,
    ReviewRequest,
    SemanticObject,
)
from .paths import DEFAULT_CONFIG, ROOT
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
        """Retrieve concepts, tables, safe joins, metrics, warnings, and provenance for a question."""
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
) -> FastAPI:
    settings = Settings.from_environment()
    resolved_bundle_path = resolve_active_bundle(bundle_path)
    bundle = load_validated_bundle(resolved_bundle_path)
    retriever = SemanticRetriever(bundle, embedder=embedder_from_environment())
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

    def swap_runtime(reviewed_path: Path) -> None:
        reviewed_bundle = load_validated_bundle(reviewed_path)
        reviewed_retriever = SemanticRetriever(reviewed_bundle, embedder=embedder_from_environment())
        reviewed_chat = (
            ChatOrchestrator(reviewed_bundle, reviewed_retriever, runtime_database_path, settings, provider_from_environment())
            if runtime_database_path is not None
            else None
        )
        runtime.update(bundle=reviewed_bundle, retriever=reviewed_retriever, chat=reviewed_chat)
        app.state.bundle = reviewed_bundle
        app.state.retriever = reviewed_retriever
        app.state.chat = reviewed_chat

    generation_manager = GenerationRunManager(
        database_path=runtime_database_path,
        provider_factory=provider_from_environment,
        config_path=source_config,
        database_schema=settings.database_schema,
        on_activate=swap_runtime,
        **({"output_root": generation_output_root} if generation_output_root is not None else {}),
        **({"reviewed_root": reviewed_output_root} if reviewed_output_root is not None else {}),
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        current = runtime["bundle"]
        return {"status": "ok", "bundle": current.name, "version": current.version, "objects": len(current.objects)}

    @app.get("/api/runtime/status")
    async def runtime_status() -> dict:
        current = runtime["bundle"]
        status = settings.public_status()
        status.update(
            {
                "database_configured": runtime_database_path is not None,
                "database_reachable": bool(runtime_database_path and runtime_database_path.is_file()),
                "bundle": current.name,
                "semantic_version": current.version,
                "generation_mode": current.generation_mode,
                "review_state": current.review_state,
                "chat_ready": bool(settings.llm_configured and runtime_database_path and runtime_database_path.is_file()),
                "resolved_response_mode": getattr(provider, "resolved_response_mode", None),
            }
        )
        return status

    @app.get("/api/bundles/active")
    async def active_bundle() -> dict:
        current = runtime["bundle"]
        counts: dict[str, int] = {}
        for obj in current.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
        return {
            "name": current.name,
            "version": current.version,
            "root": current.root,
            "counts": counts,
            "generation_mode": current.generation_mode,
            "review_state": current.review_state,
            "provider": current.provider,
            "model": current.model,
            "source_mode": current.source_mode,
            "discovery_evidence": current.discovery_evidence,
        }

    @app.get("/api/graph")
    async def graph() -> dict:
        return runtime["retriever"].graph().model_dump(mode="json")

    @app.get("/api/concepts/{concept_id}")
    async def get_concept(concept_id: str) -> SemanticObject:
        obj = runtime["retriever"].by_id.get(concept_id)
        if obj is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_concept", "id": concept_id})
        return obj

    @app.get("/api/search")
    async def search(
        q: str = Query(min_length=1),
        types: str | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict:
        requested = {item.strip() for item in types.split(",") if item.strip()} if types else None
        if requested:
            allowed = {"dataset", "table", "concept", "relationship", "metric", "policy"}
            unknown = requested - allowed
            if unknown:
                raise HTTPException(status_code=422, detail={"code": "unknown_types", "types": sorted(unknown)})
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

    @app.get("/knowledge/{document_path:path}")
    async def knowledge_document(document_path: str) -> Response:
        bundle_root = Path(runtime["bundle"].root).resolve()
        requested = (bundle_root / document_path).resolve()
        if bundle_root not in requested.parents or not requested.is_file() or requested.suffix != ".md":
            raise HTTPException(status_code=404, detail={"code": "unknown_document"})
        return Response(requested.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    app.mount("/mcp", mcp_app)
    web_dist = ROOT / "apps" / "web" / "dist"
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


app = create_app()
