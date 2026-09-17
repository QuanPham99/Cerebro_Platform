"""Regression tests for the customer self-service workspace's scoping (spec 024).

Covers both halves of the design: the object-level allowlist that shapes what the
customer-centric graph/grounding may show (`customer_scope.filter_graph_for_customer_scope`),
and the deterministic row-level filter that is the actual security boundary
(`SQLGuardrail.validate(..., customer_id=...)`).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.chat import ChatOrchestrator, DuckDBQueryExecutor, SQLGuardrail, SQLSafetyError
from cerebro.customer_scope import (
    CUSTOMER_SCOPE_OBJECT_IDS,
    filter_graph_for_customer_scope,
)
from cerebro.enrichment import GenerationProvider
from cerebro.models import (
    AnswerPayload,
    ChatMessage,
    ChatRequest,
    GraphEdge,
    GraphNode,
    GraphResponse,
    QueryPlanAndSQL,
    SQLProposal,
)
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.retrieval import SemanticRetriever
from cerebro.settings import Settings


# --- Phase A: object-level graph scope -------------------------------------------------


def test_filter_graph_for_customer_scope_drops_internal_only_nodes_and_dangling_edges():
    graph = GraphResponse(
        version="0.2.0",
        nodes=[
            GraphNode(id="entity.customer", type="entity", label="Customer"),
            GraphNode(id="entity.account", type="entity", label="Account"),
            GraphNode(id="entity.branch", type="entity", label="Branch"),
            GraphNode(id="metric.branch-fraud-exposure", type="metric", label="Branch fraud exposure"),
        ],
        edges=[
            GraphEdge(id="e1", source="entity.account", target="entity.customer", type="relationship"),
            GraphEdge(id="e2", source="entity.account", target="entity.branch", type="relationship"),
        ],
    )
    filtered = filter_graph_for_customer_scope(graph)
    node_ids = {node.id for node in filtered.nodes}
    assert node_ids == {"entity.customer", "entity.account"}
    assert [edge.id for edge in filtered.edges] == ["e1"]  # e2 dangles once entity.branch is dropped


def test_customer_scope_object_ids_exclude_internal_only_entities_and_metrics():
    excluded = {
        "entity.branch",
        "entity.employee",
        "entity.support-ticket",
        "metric.branch-fraud-exposure",
        "metric.non-performing-loan-rate",
        "metric.card-fraud-rate",
        "rule.employee-risk-portfolio-assignment",
    }
    assert excluded.isdisjoint(CUSTOMER_SCOPE_OBJECT_IDS)
    assert {"entity.customer", "entity.account", "entity.loan", "entity.transaction"} <= CUSTOMER_SCOPE_OBJECT_IDS


def test_api_graph_endpoint_customer_scope_excludes_internal_objects(bank_source_config: Path, tmp_path: Path):
    from cerebro.api import create_app

    app = create_app(source_config=bank_source_config, generation_output_root=tmp_path / "generated")
    graph_endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/graph")

    import asyncio

    full = asyncio.run(graph_endpoint(scope=None, tier="all", node_id=None, depth=1))
    scoped = asyncio.run(graph_endpoint(scope="customer", tier="all", node_id=None, depth=1))
    full_ids = {node["id"] for node in full["nodes"]}
    scoped_ids = {node["id"] for node in scoped["nodes"]}
    assert "entity.branch" in full_ids or "entity.employee" in full_ids  # sanity: full graph has internal nodes
    assert scoped_ids <= CUSTOMER_SCOPE_OBJECT_IDS
    assert "entity.branch" not in scoped_ids
    assert "metric.branch-fraud-exposure" not in scoped_ids


def test_api_graph_endpoint_customer_scope_composes_with_overview_tier(bank_source_config: Path, tmp_path: Path):
    from cerebro.api import create_app

    app = create_app(source_config=bank_source_config, generation_output_root=tmp_path / "generated")
    graph_endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/graph")

    import asyncio

    scoped_overview = asyncio.run(graph_endpoint(scope="customer", tier="overview", node_id=None, depth=1))
    scoped_ids = {node["id"] for node in scoped_overview["nodes"]}
    assert scoped_ids  # the overview tier still surfaces the customer's own domains/entities
    assert scoped_ids <= CUSTOMER_SCOPE_OBJECT_IDS
    assert "domain.operations" not in scoped_ids  # internal-only domain, excluded by the customer allowlist
    assert "entity.branch" not in scoped_ids


# --- Phase B: deterministic row-level filter (the actual security boundary) ------------


@pytest.fixture()
def two_customer_database(tmp_path: Path) -> Path:
    path = tmp_path / "two_customer.duckdb"
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    connection = duckdb.connect(str(path))
    try:
        for table in [item for item in bundle.objects if item.profile_kind == "physical_table"]:
            columns = table.cerebro.get("columns", [])
            definition = ", ".join(f'"{column["name"]}" {column["data_type"]}' for column in columns)
            connection.execute(f'CREATE TABLE "{table.id.removeprefix("table.")}" ({definition})')
        connection.execute(
            "INSERT INTO customers (customer_id, gender) VALUES (1, 'Female'), (2, 'Male')"
        )
        connection.execute(
            "INSERT INTO accounts (account_id, customer_id, balance) VALUES (1, 1, 100.0), (2, 2, 300.0)"
        )
        connection.execute(
            "INSERT INTO transactions (transaction_id, account_id, amount) VALUES (1, 1, 50.0), (2, 2, 75.0)"
        )
        connection.execute(
            "INSERT INTO loans (loan_id, customer_id, loan_amount) VALUES (1, 1, 1000.0), (2, 2, 2000.0)"
        )
    finally:
        connection.close()
    return path


def _guardrail() -> SQLGuardrail:
    return SQLGuardrail(load_validated_bundle(DEFAULT_BUNDLE))


def test_row_filter_scopes_unqualified_query_to_the_logged_in_customer(two_customer_database: Path):
    # balance is a confidential column, so even the customer's own workspace query must
    # aggregate it (unrelated to row-level scoping — the same rule applies unscoped).
    sql = _guardrail().validate("SELECT SUM(balance) AS total_balance FROM accounts", customer_id="1")
    executor = DuckDBQueryExecutor(two_customer_database)
    columns, rows, _truncated = executor.execute(sql)
    assert columns == ["total_balance"]
    assert rows == [[100.0]]  # only customer 1's account, never customer 2's


def test_row_filter_scopes_fk_chained_table(two_customer_database: Path):
    sql = _guardrail().validate("SELECT amount FROM transactions", customer_id="2")
    executor = DuckDBQueryExecutor(two_customer_database)
    columns, rows, _truncated = executor.execute(sql)
    assert rows == [[75.0]]  # transactions has no customer_id column; scoped via accounts


def test_row_filter_survives_an_explicit_cross_customer_where_clause(two_customer_database: Path):
    # Simulated prompt injection: the proposed SQL itself names a different customer_id.
    # The row filter is applied to the base table scan, so this can only ever return
    # customer 1's own (now-empty-after-intersection) result — never customer 2's row.
    sql = _guardrail().validate(
        "SELECT SUM(balance) AS total_balance FROM accounts WHERE customer_id = 2", customer_id="1"
    )
    executor = DuckDBQueryExecutor(two_customer_database)
    _columns, rows, _truncated = executor.execute(sql)
    assert rows == [[None]]  # SUM over zero rows, never customer 2's balance


def test_row_filter_scopes_aggregates_to_the_logged_in_customer_alone(two_customer_database: Path):
    sql = _guardrail().validate("SELECT COUNT(*) AS total FROM accounts", customer_id="1")
    executor = DuckDBQueryExecutor(two_customer_database)
    _columns, rows, _truncated = executor.execute(sql)
    assert rows == [[1]]  # aggregating over the filtered base table, not all customers


def test_branches_and_employees_are_rejected_in_customer_mode():
    guardrail = _guardrail()
    with pytest.raises(SQLSafetyError, match="not available in the customer workspace"):
        guardrail.validate("SELECT branch_id FROM branches", customer_id="1")
    with pytest.raises(SQLSafetyError, match="not available in the customer workspace"):
        guardrail.validate("SELECT employee_id FROM employees", customer_id="1")


def test_branches_still_allowed_outside_customer_mode():
    # Data Engineer / PowerBI path (customer_id=None) is completely unaffected.
    guardrail = _guardrail()
    assert "branches" in guardrail.validate("SELECT branch_id FROM branches")


# --- Metadata-exploration shortcut must not bypass customer scoping --------------------


class _StubProvider(GenerationProvider):
    name = "stub"
    model = "customer-scope-fixture"

    def generate(self, schema_name, prompt, output_model):
        if output_model is QueryPlanAndSQL:
            return QueryPlanAndSQL(
                intent="Look up my own balance",
                tables=["accounts"],
                sql="SELECT balance FROM accounts",
                explanation="Own balance",
            )
        if output_model is SQLProposal:
            return SQLProposal(sql="SELECT balance FROM accounts", explanation="Own balance")
        if output_model is AnswerPayload:
            return AnswerPayload(answer="Your balance is shown above.")
        raise AssertionError(schema_name)


def test_metadata_exploration_shortcut_is_disabled_for_customer_scoped_requests(two_customer_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    settings = Settings(
        database_path=two_customer_database,
        database_schema="main",
        llm_base_url="http://example.test/v1",
        llm_api_key="secret",
        llm_model="fixture",
        llm_response_mode="json_schema",
        embedding_model=None,
    )
    orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, settings, _StubProvider())

    # Without a customer_id, this phrasing hits the metadata-exploration shortcut and dumps
    # every live table schema plus every bundle object id (unrestricted Data Engineer path).
    unscoped = orchestrator.chat(ChatRequest(message="What tables are available to explore?"))
    assert any(t.agent == "catalog_inspection" for t in unscoped.trace)

    # The same phrasing, scoped to a logged-in customer, must go through the normal
    # grounding + SQL-guardrail path instead of the catalog-dumping shortcut.
    scoped = orchestrator.chat(
        ChatRequest(message="What tables are available to explore?", customer_id="1")
    )
    assert not any(t.agent == "catalog_inspection" for t in scoped.trace)


# --- Spec 027: trimmed metadata + question-text-keyed plan cache -----------------------


def _settings(path: Path) -> Settings:
    return Settings(
        database_path=path,
        database_schema="main",
        llm_base_url="http://example.test/v1",
        llm_api_key="secret",
        llm_model="fixture",
        llm_response_mode="json_schema",
        embedding_model=None,
    )


class _CountingProvider(GenerationProvider):
    name = "counting"
    model = "customer-scope-fixture"

    def __init__(self):
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    def generate(self, schema_name, prompt, output_model):
        self.calls.append(schema_name)
        self.prompts.append((schema_name, prompt))
        if output_model is QueryPlanAndSQL:
            return QueryPlanAndSQL(
                intent="Look up my own balance",
                tables=["accounts"],
                sql="SELECT SUM(balance) AS total_balance FROM accounts",
                explanation="Own balance",
            )
        if output_model is SQLProposal:
            return SQLProposal(sql="SELECT SUM(balance) AS total_balance FROM accounts", explanation="Own balance")
        if output_model is AnswerPayload:
            return AnswerPayload(answer="Your balance is shown above.")
        raise AssertionError(schema_name)


def test_available_metadata_and_exploration_inventory_skipped_for_customer_requests(
    two_customer_database: Path, monkeypatch: pytest.MonkeyPatch
):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    provider = _CountingProvider()
    orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, _settings(two_customer_database), provider)

    inventory_calls: list[None] = []
    original_inventory = orchestrator._exploration_inventory

    def spy(*args, **kwargs):
        inventory_calls.append(None)
        return original_inventory(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "_exploration_inventory", spy)

    orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="1"))
    assert inventory_calls == []
    customer_prompt = next(prompt for schema, prompt in provider.prompts if schema == "query_plan")
    assert '"available_metadata"' not in customer_prompt

    prompts_before_internal_call = len(provider.prompts)
    orchestrator.chat(ChatRequest(message="What is the total balance across all customers?"))
    assert inventory_calls == [None]
    internal_prompt = next(
        prompt
        for schema, prompt in provider.prompts[prompts_before_internal_call:]
        if schema == "query_plan"
    )
    assert '"available_metadata"' in internal_prompt


def test_plan_cache_reuses_query_plan_for_repeated_customer_question_across_customers(two_customer_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    provider = _CountingProvider()
    orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, _settings(two_customer_database), provider)

    first = orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="1"))
    second = orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="2"))

    assert provider.calls.count("query_plan") == 1
    assert provider.calls.count("database_answer") == 2
    # The row-level filter still scopes each customer to their own data on a cache hit.
    assert first.rows == [[100.0]]
    assert second.rows == [[300.0]]


def test_plan_cache_does_not_apply_when_history_is_present(two_customer_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    provider = _CountingProvider()
    orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, _settings(two_customer_database), provider)
    history = [ChatMessage(role="user", content="Hello")]

    orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="1", history=history))
    orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="1", history=history))

    assert provider.calls.count("query_plan") == 2


def test_plan_cache_does_not_apply_to_internal_requests(two_customer_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    provider = _CountingProvider()
    orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, _settings(two_customer_database), provider)

    orchestrator.chat(ChatRequest(message="What is the total balance across all customers?"))
    orchestrator.chat(ChatRequest(message="What is the total balance across all customers?"))

    assert provider.calls.count("query_plan") == 2


def test_plan_cache_is_not_shared_across_orchestrator_instances(two_customer_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    settings = _settings(two_customer_database)

    first_provider = _CountingProvider()
    first_orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, settings, first_provider)
    assert first_orchestrator._plan_cache == {}
    first_orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="1"))
    assert first_orchestrator._plan_cache

    second_provider = _CountingProvider()
    second_orchestrator = ChatOrchestrator(bundle, retriever, two_customer_database, settings, second_provider)
    assert second_orchestrator._plan_cache == {}
    second_orchestrator.chat(ChatRequest(message="What is my balance?", customer_id="2"))

    assert second_provider.calls.count("query_plan") == 1


# --- Spec 028: loan balance/maturity grounding objects are customer-visible ------------


def test_customer_scope_object_ids_include_loan_balance_and_maturity_grounding():
    assert {
        "metric.customer-loan-principal-paid-total",
        "rule.loan-maturity-date",
    } <= CUSTOMER_SCOPE_OBJECT_IDS


def test_customer_scoped_grounding_surfaces_loan_principal_paid_metric():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    grounding = retriever.grounding(
        "How much principal have I paid off on my loan so far?",
        allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS,
    )
    assert "metric.customer-loan-principal-paid-total" in {item["id"] for item in grounding.metrics}


def test_customer_scoped_grounding_surfaces_loan_maturity_rule():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    grounding = retriever.grounding(
        "When will my loan reach its maturity date?",
        allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS,
    )
    assert "rule.loan-maturity-date" in {item["id"] for item in grounding.rules}


# --- Spec 029: Vietnamese-language customer questions retrieve real grounding ----------


def test_vietnamese_customer_question_retrieves_grounding_via_lexical_aliases():
    # No embedder configured (lexical-only), matching the dev-server environment where this
    # was originally found empty (evidence_ids: []) before the tokenizer fix + Vietnamese
    # aliases were added.
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    retriever = SemanticRetriever(bundle)
    grounding = retriever.grounding(
        "Tôi có bao nhiêu tài khoản đang hoạt động?",
        allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS,
    )
    selected_ids = {
        item["id"]
        for group in (grounding.entities, grounding.tables, grounding.rules)
        for item in group
    }
    assert selected_ids & {"table.accounts", "entity.account", "rule.active-customer"}
