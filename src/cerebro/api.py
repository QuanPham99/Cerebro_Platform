from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from .bundle import load_validated_bundle
from .models import GroundingResponse, SemanticObject
from .paths import DEFAULT_BUNDLE, ROOT
from .retrieval import SemanticRetriever, embedder_from_environment


def create_mcp_server(retriever: SemanticRetriever) -> FastMCP:
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
        return retriever.grounding(question, min(max(limit, 1), 50)).model_dump(mode="json")

    @server.tool()
    def get_concept(concept_id: str) -> dict:
        """Return one semantic object by its stable OKF identifier."""
        obj = retriever.by_id.get(concept_id)
        if obj is None:
            raise ValueError(f"Unknown concept ID: {concept_id}")
        return obj.model_dump(mode="json")

    @server.tool()
    def expand_neighborhood(concept_ids: list[str], depth: int = 1) -> dict:
        """Expand stable identifiers over typed semantic graph edges."""
        if depth < 0 or depth > 3:
            raise ValueError("depth must be between 0 and 3")
        unknown = sorted(set(concept_ids) - set(retriever.by_id))
        if unknown:
            raise ValueError(f"Unknown concept IDs: {', '.join(unknown)}")
        ids = retriever.expand(concept_ids, depth)
        return {"semantic_version": retriever.bundle.version, "depth": depth, "concept_ids": ids}

    return server


def create_app(bundle_path: Path | str = DEFAULT_BUNDLE) -> FastAPI:
    bundle = load_validated_bundle(bundle_path)
    retriever = SemanticRetriever(bundle, embedder=embedder_from_environment())
    mcp_server = create_mcp_server(retriever)
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(
        title="Cerebro Semantic Layer",
        version=bundle.version,
        description="Explainable OKF grounding API; no SQL execution and no source-row access.",
        lifespan=lifespan,
    )
    app.state.bundle = bundle
    app.state.retriever = retriever
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "bundle": bundle.name, "version": bundle.version, "objects": len(bundle.objects)}

    @app.get("/api/bundles/active")
    async def active_bundle() -> dict:
        counts: dict[str, int] = {}
        for obj in bundle.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
        return {"name": bundle.name, "version": bundle.version, "root": bundle.root, "counts": counts, "generation_mode": "fallback"}

    @app.get("/api/graph")
    async def graph() -> dict:
        return retriever.graph().model_dump(mode="json")

    @app.get("/api/concepts/{concept_id}")
    async def get_concept(concept_id: str) -> SemanticObject:
        obj = retriever.by_id.get(concept_id)
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
            "results": [item.model_dump(mode="json") for item in retriever.search(q, limit, requested)],
        }

    @app.post("/api/grounding", response_model=GroundingResponse)
    async def grounding(payload: dict) -> GroundingResponse:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=422, detail={"code": "empty_question"})
        return retriever.grounding(question, int(payload.get("limit", 10)))

    @app.get("/knowledge/{document_path:path}")
    async def knowledge_document(document_path: str) -> FileResponse:
        bundle_root = Path(bundle.root).resolve()
        requested = (bundle_root / document_path).resolve()
        if bundle_root not in requested.parents or not requested.is_file() or requested.suffix != ".md":
            raise HTTPException(status_code=404, detail={"code": "unknown_document"})
        return FileResponse(requested, media_type="text/markdown; charset=utf-8")

    app.mount("/mcp", mcp_app)
    web_dist = ROOT / "apps" / "web" / "dist"
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


app = create_app()
