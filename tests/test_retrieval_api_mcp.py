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


def test_graph_edges_are_canonical_and_directional():
    graph = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE)).graph()
    edges = {(edge.source, edge.target, edge.type, edge.label) for edge in graph.edges}
    assert ("dataset.bank-workshop", "table.accounts", "semantic_mapping", "contains") in edges
    assert ("concept.card-fraud", "table.card_transactions", "semantic_mapping", "maps to") in edges
    assert ("metric.card-fraud-rate", "table.card_transactions", "metric_dependency", "depends on") in edges
    assert ("policy.sensitive-banking-data", "table.accounts", "policy_coverage", "applies to") in edges
    assert ("table.accounts", "table.customers", "physical_fk", "many-to-one") in edges
    assert ("relationship.account_customer", "table.accounts", "relationship_endpoint", "endpoint") in edges
    assert not any(source == "table.card_transactions" and target == "concept.card-fraud" for source, target, _, _ in edges)
    assert not any(
        source == "relationship.account_customer" and target == "table.accounts" and kind == "semantic_mapping"
        for source, target, kind, _ in edges
    )
    assert len(edges) == len(graph.edges)


def test_all_golden_questions_pass():
    results = run_evaluation()
    assert len(results) == 10
    assert all(item["passed"] for item in results), results


def test_http_contracts_and_typed_errors(bank_source_config, tmp_path):
    async def exercise():
        transport = httpx.ASGITransport(app=create_app(
            source_config=bank_source_config,
            generation_output_root=tmp_path / "generated",
        ))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/health")).json()["objects"] == 36
            runtime = (await client.get("/api/runtime/status")).json()
            assert runtime["bundle"] == "bank-workshop"
            assert "api_key" not in runtime
            blocked_chat = await client.post("/api/chat", json={"message": "customer count", "history": []})
            assert blocked_chat.status_code == 200
            assert blocked_chat.json()["status"] == "blocked"
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

            started = await client.post("/api/generation/runs")
            assert started.status_code == 202
            run_id = started.json()["id"]
            for _ in range(100):
                run = (await client.get(f"/api/generation/runs/{run_id}")).json()
                if run["status"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.02)
            assert run["status"] == "succeeded", run
            assert run["candidate"]["generation_mode"] == "fallback"
            assert [event["stage"] for event in run["events"] if event["status"] == "skipped"] == [
                "business_semantics", "relationship_semantics", "query_semantics",
            ]
            event_stream = await client.get(f"/api/generation/runs/{run_id}/events")
            assert event_stream.status_code == 200
            assert "event: progress" in event_stream.text
            assert "event: complete" in event_stream.text
            assert str(bank_source_config) not in event_stream.text
            candidate_graph = await client.get(f"/api/generation/runs/{run_id}/graph")
            assert candidate_graph.status_code == 200
            assert candidate_graph.json()["version"] == run["candidate"]["version"]
            assert (await client.get(f"/api/generation/runs/{run_id}/concepts/table.customers")).status_code == 200
            assert (await client.get("/api/generation/runs/missing")).status_code == 404

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
