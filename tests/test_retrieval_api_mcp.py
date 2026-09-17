import asyncio
import json

import httpx

from cerebro.api import create_app, create_mcp_server
from cerebro.bundle import load_validated_bundle
from cerebro.evaluation import run_evaluation, summarize_evaluation
from cerebro.paths import DEFAULT_BUNDLE, ROOT
from cerebro.retrieval import SemanticRetriever


def test_retrieval_fusion_graph_and_embedding_fallback():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    graph = retriever.graph()
    assert not ({node.id for node in graph.nodes} & {edge.id for edge in graph.edges})
    results = retriever.search("fraud rate by card type", 10)
    assert {"metric.card-fraud-rate", "dimension.card-type"} <= {item.id for item in results}
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


def test_grounding_includes_a_second_business_rule_when_it_genuinely_matches():
    # Regression for spec 015: a rule the question genuinely names (positive lexical/vector
    # name-term overlap) must not be excluded merely because a different kind's object claimed
    # the sole requested_kinds slot for this question.
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    grounding = retriever.grounding(
        "List customers who qualify as high-value multichannel customers along with their net cash flow."
    )
    rule_ids = {rule["id"] for rule in grounding.rules}
    assert "rule.high-value-multichannel-customer" in rule_ids
    # Same-kind candidates that score zero for this question must not ride along just because the
    # cap was raised.
    assert rule_ids == {"rule.high-value-multichannel-customer"}


def test_grounding_rules_stay_empty_and_unbloated_for_unrelated_questions():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    assert retriever.grounding("How many customers are there by gender?").rules == []
    # A rule that merely shares one incidental word with the question (e.g. "fraud" in both
    # "fraud rate by card type" and the unrelated rule.branch-fraud-escalation) must not be
    # pulled in — only a strong, name-covering match earns the bypass.
    fraud_by_card = retriever.grounding("fraud rate by card type")
    assert fraud_by_card.rules == []
    total_selected = sum(len(items) for items in (
        fraud_by_card.entities, fraud_by_card.dimensions, fraud_by_card.metrics,
        fraud_by_card.rules, fraud_by_card.tables, fraud_by_card.joins, fraud_by_card.concepts,
    ))
    assert total_selected <= 10


def test_graph_edges_are_canonical_and_directional():
    graph = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE)).graph()
    edges = {(edge.source, edge.target, edge.type, edge.label) for edge in graph.edges}
    assert ("dataset.bank-workshop", "table.accounts", "semantic_mapping", "contains") in edges
    assert ("entity.card-transaction", "table.card_transactions", "entity_mapping", "maps to") in edges
    assert ("dimension.card-type", "entity.card", "dimension_entity", "describes") in edges
    assert ("metric.card-fraud-rate", "table.card_transactions", "metric_dependency", "depends on") in edges
    assert ("policy.sensitive-banking-data", "table.accounts", "policy_coverage", "applies to") in edges
    assert ("table.accounts", "table.customers", "physical_fk", "many-to-one") in edges
    assert ("relationship.account_customer", "table.accounts", "relationship_endpoint", "endpoint") in edges
    assert ("entity.card-transaction", "entity.card", "semantic_relationship", "many-to-one") in edges
    assert ("entity.account", "domain.retail-banking", "domain_membership", "belongs to") in edges
    assert not any(source == "table.card_transactions" and target == "entity.card-transaction" for source, target, _, _ in edges)
    assert not any(
        source == "relationship.account_customer" and target == "table.accounts" and kind == "semantic_mapping"
        for source, target, kind, _ in edges
    )
    assert len(edges) == len(graph.edges)


def test_graph_overview_tier_returns_only_domain_and_entity_nodes():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    overview = retriever.graph(tier="overview")
    kinds = {node.profile_kind for node in overview.nodes}
    assert kinds == {"domain", "entity"}
    assert len(overview.nodes) == 14  # 4 domains + 10 entities
    # every edge must still resolve to two overview-tier nodes
    node_ids = {node.id for node in overview.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in overview.edges)
    full = retriever.graph(tier="all")
    assert len(full.nodes) == len(retriever.bundle.objects)
    assert retriever.graph().nodes == full.nodes  # tier=None/omitted defaults to unrestricted at the retriever layer


def test_graph_node_id_depth_expansion_reuses_adjacency_bfs():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    one_hop = retriever.graph(node_id="entity.account", depth=1)
    one_hop_ids = {node.id for node in one_hop.nodes}
    assert one_hop_ids == set(retriever.expand(["entity.account"], depth=1))
    assert "entity.account" in one_hop_ids
    assert "domain.retail-banking" in one_hop_ids
    two_hop = retriever.graph(node_id="entity.account", depth=2)
    assert one_hop_ids <= {node.id for node in two_hop.nodes}


def test_retriever_path_finds_shortest_route_and_handles_unreachable_or_unknown_ids():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    route = retriever.path("entity.account", "entity.customer")
    assert route is not None
    assert route[0] == "entity.account"
    assert route[-1] == "entity.customer"
    assert retriever.path("entity.account", "entity.account") == ["entity.account"]
    assert retriever.path("entity.account", "missing.id") is None


def test_all_golden_questions_pass():
    semantic = run_evaluation()
    legacy = run_evaluation(questions_path=ROOT / "evaluation" / "golden-questions.yaml")
    assert len(semantic) == 30
    assert len(legacy) == 10
    assert all(item["passed"] for item in [*semantic, *legacy]), [*semantic, *legacy]
    summary = summarize_evaluation(semantic)
    assert summary["cases"] == {"passed": 30, "total": 30, "accuracy": 1.0}
    assert summary["join_path_accuracy"] == 1.0
    assert all(item["accuracy"] == 1.0 for item in summary["by_kind"].values())


def test_http_contracts_and_typed_errors(bank_source_config, tmp_path):
    async def exercise():
        transport = httpx.ASGITransport(app=create_app(
            source_config=bank_source_config,
            generation_output_root=tmp_path / "generated",
        ))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = (await client.get("/api/health")).json()
            assert health["objects"] == 75
            runtime = (await client.get("/api/runtime/status")).json()
            assert runtime["bundle"] == "bank-workshop"
            assert "api_key" not in runtime
            blocked_chat = await client.post("/api/chat", json={"message": "customer count", "history": []})
            assert blocked_chat.status_code == 200
            assert blocked_chat.json()["status"] == "blocked"
            schema_chat = await client.post(
                "/api/chat",
                json={"message": "What tables are avialable to query?", "history": []},
            )
            assert schema_chat.status_code == 200
            assert schema_chat.json()["status"] == "answered"
            assert schema_chat.json()["row_count"] == 75
            assert {row[1] for row in schema_chat.json()["rows"]} >= {
                "table.accounts",
                "table.customers",
                "table.transactions",
                "dataset.bank-workshop",
            }
            default_graph = (await client.get("/api/graph")).json()
            assert {node["profile_kind"] for node in default_graph["nodes"]} <= {"domain", "entity"}
            assert len(default_graph["nodes"]) == 14  # 4 domains + 10 entities, the default overview tier
            assert len((await client.get("/api/graph", params={"tier": "all"})).json()["nodes"]) == 75
            assert (await client.get("/api/graph", params={"tier": "unknown"})).status_code == 422
            expanded = (
                await client.get("/api/graph", params={"node_id": "entity.account", "depth": 1})
            ).json()
            expanded_ids = {node["id"] for node in expanded["nodes"]}
            assert "entity.account" in expanded_ids
            assert "domain.retail-banking" in expanded_ids
            path = (
                await client.get("/api/graph/path", params={"from": "entity.account", "to": "entity.customer"})
            ).json()
            assert path["path"][0] == "entity.account"
            assert path["path"][-1] == "entity.customer"
            assert (await client.get("/api/graph/path", params={"from": "entity.account", "to": "missing"})).status_code == 404
            assert (await client.get("/api/concepts/table.accounts")).status_code == 200
            assert (await client.get("/api/concepts/missing")).status_code == 404
            search = await client.get(
                "/api/search", params={"q": "late payment", "types": "metric,concept"}
            )
            assert search.status_code == 200
            assert {item["profile_kind"] for item in search.json()["results"]} <= {"metric", "legacy_concept"}
            assert (await client.get("/api/search", params={"q": "loan", "types": "unknown"})).status_code == 422
            response = await client.post("/api/grounding", json={"question": "fraud rate by card type"})
            assert response.status_code == 200
            assert response.json()["semantic_version"] == "0.2.0"
            assert {item["id"] for item in response.json()["entities"]} >= {"entity.card-transaction", "entity.card"}

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
        assert grounding["semantic_version"] == "0.2.0"
        concept = json.loads((await server.call_tool("get_concept", {"concept_id": "entity.card-transaction"}))[0].text)
        assert concept["id"] == "entity.card-transaction"
        expanded = json.loads((await server.call_tool("expand_neighborhood", {"concept_ids": ["entity.card-transaction"], "depth": 1}))[0].text)
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
