import asyncio
import json

import httpx

from cerebro.api import create_app, create_mcp_server
from cerebro.bundle import load_validated_bundle
from cerebro.evaluation import run_evaluation
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.retrieval import SemanticRetriever


def test_retrieval_fusion_graph_and_embedding_fallback():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    graph = retriever.graph()
    assert not ({node.id for node in graph.nodes} & {edge.id for edge in graph.edges})
    results = retriever.search("fraud rate by card type", 10)
    assert {"concept.card-fraud", "metric.card-fraud-rate"} <= {
        item.id for item in results
    }
    grounding = retriever.grounding("total transaction amount by branch")
    join_ids = {join["id"] for join in grounding.joins}
    assert {
        "relationship.transaction_account",
        "relationship.account_branch",
    } <= join_ids
    fallback = SemanticRetriever(
        bundle, embedder=lambda _: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    assert fallback.grounding("customer count").retrieval_mode == "lexical_graph"
    hybrid = SemanticRetriever(
        bundle,
        embedder=lambda texts: [
            [float("fraud" in text.lower()), 1.0] for text in texts
        ],
    )
    hybrid_result = hybrid.grounding("fraud rate")
    assert hybrid_result.retrieval_mode == "hybrid_graph"
    assert any(
        reason.startswith("vector:")
        for item in hybrid_result.ranking_evidence
        for reason in item.evidence
    )


def test_all_golden_questions_pass():
    results = run_evaluation()
    assert len(results) == 10
    assert all(item["passed"] for item in results), results


def test_http_contracts_and_typed_errors():
    async def exercise():
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            assert (await client.get("/api/health")).json()["objects"] == 36
            assert len((await client.get("/api/graph")).json()["nodes"]) == 36
            assert (await client.get("/api/concepts/table.accounts")).status_code == 200
            assert (await client.get("/api/concepts/missing")).status_code == 404
            search = await client.get(
                "/api/search", params={"q": "late payment", "types": "metric,concept"}
            )
            assert search.status_code == 200
            assert {item["type"] for item in search.json()["results"]} <= {
                "metric",
                "concept",
            }
            assert (
                await client.get(
                    "/api/search", params={"q": "loan", "types": "unknown"}
                )
            ).status_code == 422
            response = await client.post(
                "/api/grounding", json={"question": "fraud rate by card type"}
            )
            assert response.status_code == 200
            assert response.json()["semantic_version"] == "0.1.0"

    asyncio.run(exercise())


def test_mcp_tools_share_grounding_contract():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    server = create_mcp_server(retriever)

    async def exercise():
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {
            "retrieve_grounding",
            "get_concept",
            "expand_neighborhood",
        }
        grounding = json.loads(
            (
                await server.call_tool(
                    "retrieve_grounding",
                    {"question": "card fraud percentage", "limit": 10},
                )
            )[0].text
        )
        assert grounding["semantic_version"] == "0.1.0"
        concept = json.loads(
            (
                await server.call_tool(
                    "get_concept", {"concept_id": "concept.card-fraud"}
                )
            )[0].text
        )
        assert concept["id"] == "concept.card-fraud"
        expanded = json.loads(
            (
                await server.call_tool(
                    "expand_neighborhood",
                    {"concept_ids": ["concept.card-fraud"], "depth": 1},
                )
            )[0].text
        )
        assert "table.card_transactions" in expanded["concept_ids"]

    asyncio.run(exercise())


def test_grounding_endpoints_stay_advisory_and_expose_no_sql_route():
    """`/api/grounding` and MCP remain metadata retrieval, not authorization."""
    app = create_app(DEFAULT_BUNDLE)
    paths = {route.path for route in app.routes}
    assert "/api/grounding" in paths
    assert not {path for path in paths if "sql" in path.lower()}

    server = create_mcp_server(DEFAULT_BUNDLE)
    tools = asyncio.run(server.list_tools())
    assert not {tool.name for tool in tools if "sql" in tool.name.lower()}


def test_advisory_grounding_payload_is_not_an_authorization_scope():
    """A serialized GroundingResponse can never stand in for a trusted scope."""
    import pytest
    from pydantic import ValidationError

    from cerebro.models import SQLGenerationRequest

    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    grounding = retriever.grounding("fraud rate by card type")
    assert not hasattr(grounding, "authorization_scope_hash")
    with pytest.raises(ValidationError):
        SQLGenerationRequest(
            question="fraud rate by card type",
            authorization_scope=json.loads(grounding.model_dump_json()),
        )


def test_health_reports_whether_the_web_ui_is_built():
    """One process serves UI, API, and MCP, so its state must be observable."""

    async def exercise() -> dict:
        transport = httpx.ASGITransport(app=create_app(DEFAULT_BUNDLE))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return (await client.get("/api/health")).json()

    payload = asyncio.run(exercise())
    assert payload["status"] == "ok"
    assert payload["web_ui"] in {"built", "not_built"}


def test_unbuilt_web_ui_explains_itself_instead_of_a_bare_404(monkeypatch, tmp_path):
    """A missing UI bundle reads like a broken server unless it says otherwise."""
    from cerebro import api

    monkeypatch.setattr(api, "ROOT", tmp_path)

    async def exercise():
        transport = httpx.ASGITransport(app=api.create_app(DEFAULT_BUNDLE))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return (
                await client.get("/"),
                (await client.get("/api/health")).json(),
            )

    response, health = asyncio.run(exercise())
    assert health["web_ui"] == "not_built"
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "web_ui_not_built"
    assert "npm run build" in body["build_command"]


def test_built_web_ui_is_served_from_the_same_process(monkeypatch, tmp_path):
    from cerebro import api

    dist = tmp_path / "apps" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<title>Cerebro</title>", encoding="utf-8")
    monkeypatch.setattr(api, "ROOT", tmp_path)

    async def exercise():
        transport = httpx.ASGITransport(app=api.create_app(DEFAULT_BUNDLE))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return (
                await client.get("/"),
                (await client.get("/api/health")).json(),
                await client.get("/api/bundles/active"),
            )

    page, health, active = asyncio.run(exercise())
    assert page.status_code == 200
    assert "Cerebro" in page.text
    assert health["web_ui"] == "built"
    # Mounting the UI at the root must not shadow the API or MCP routes.
    assert active.status_code == 200
