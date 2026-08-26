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
    assert {"concept.card-fraud", "metric.card-fraud-rate"} <= {item.id for item in results}
    grounding = retriever.grounding("total transaction amount by branch")
    join_ids = {join["id"] for join in grounding.joins}
    assert {"relationship.transaction_account", "relationship.account_branch"} <= join_ids
    fallback = SemanticRetriever(bundle, embedder=lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
    assert fallback.grounding("customer count").retrieval_mode == "lexical_graph"
    hybrid = SemanticRetriever(
        bundle,
        embedder=lambda texts: [[float("fraud" in text.lower()), 1.0] for text in texts],
    )
    hybrid_result = hybrid.grounding("fraud rate")
    assert hybrid_result.retrieval_mode == "hybrid_graph"
    assert any(reason.startswith("vector:") for item in hybrid_result.ranking_evidence for reason in item.evidence)


def test_all_golden_questions_pass():
    results = run_evaluation()
    assert len(results) == 10
    assert all(item["passed"] for item in results), results


def test_http_contracts_and_typed_errors():
    async def exercise():
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/health")).json()["objects"] == 36
            assert len((await client.get("/api/graph")).json()["nodes"]) == 36
            assert (await client.get("/api/concepts/table.accounts")).status_code == 200
            assert (await client.get("/api/concepts/missing")).status_code == 404
            search = await client.get("/api/search", params={"q": "late payment", "types": "metric,concept"})
            assert search.status_code == 200
            assert {item["type"] for item in search.json()["results"]} <= {"metric", "concept"}
            assert (await client.get("/api/search", params={"q": "loan", "types": "unknown"})).status_code == 422
            response = await client.post("/api/grounding", json={"question": "fraud rate by card type"})
            assert response.status_code == 200
            assert response.json()["semantic_version"] == "0.1.0"

    asyncio.run(exercise())


def test_mcp_tools_share_grounding_contract():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    server = create_mcp_server(retriever)

    async def exercise():
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {"retrieve_grounding", "get_concept", "expand_neighborhood"}
        grounding = json.loads((await server.call_tool("retrieve_grounding", {"question": "card fraud percentage", "limit": 10}))[0].text)
        assert grounding["semantic_version"] == "0.1.0"
        concept = json.loads((await server.call_tool("get_concept", {"concept_id": "concept.card-fraud"}))[0].text)
        assert concept["id"] == "concept.card-fraud"
        expanded = json.loads((await server.call_tool("expand_neighborhood", {"concept_ids": ["concept.card-fraud"], "depth": 1}))[0].text)
        assert "table.card_transactions" in expanded["concept_ids"]

    asyncio.run(exercise())
