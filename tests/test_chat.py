from pathlib import Path

import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.chat import ChatOrchestrator, SQLGuardrail, SQLSafetyError
from cerebro.enrichment import GenerationProvider
from cerebro.models import AnswerPayload, ChatRequest, QueryPlan, SQLProposal
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.retrieval import SemanticRetriever
from cerebro.settings import Settings


class ChatProvider(GenerationProvider):
    name = "mock"
    model = "chat-fixture"

    def __init__(self):
        self.prompts: list[tuple[str, str]] = []

    def generate(self, schema_name, prompt, output_model):
        self.prompts.append((schema_name, prompt))
        if output_model is QueryPlan:
            return QueryPlan(intent="Count customers by gender", tables=["customers"], group_by=["gender"])
        if output_model is SQLProposal:
            return SQLProposal(sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender", explanation="Safe aggregate")
        if output_model is AnswerPayload:
            return AnswerPayload(answer="There are two female customers and one male customer.")
        raise AssertionError(schema_name)


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


def test_sql_guardrail_allows_aggregates_and_blocks_sensitive_or_writes():
    guardrail = SQLGuardrail(load_validated_bundle(DEFAULT_BUNDLE))
    assert "LIMIT 100" in guardrail.validate("SELECT gender, COUNT(*) FROM customers GROUP BY gender")
    assert "AVG(balance)" in guardrail.validate("SELECT AVG(balance) FROM accounts")
    for sql in ("SELECT name FROM customers", "SELECT balance FROM accounts", "DELETE FROM customers", "SELECT * FROM customers"):
        with pytest.raises(SQLSafetyError):
            guardrail.validate(sql)
    approved = "SELECT a.account_id, c.gender FROM accounts a JOIN customers c ON a.customer_id = c.customer_id"
    assert "JOIN customers" in guardrail.validate(approved)
    with pytest.raises(SQLSafetyError, match="approved semantic relationship"):
        guardrail.validate("SELECT a.account_id, c.gender FROM accounts a JOIN customers c ON a.account_id = c.customer_id")


def test_chat_runs_validated_read_only_query(bank_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    provider = ChatProvider()
    response = ChatOrchestrator(
        bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), provider
    ).chat(ChatRequest(message="How many customers are there by gender?"))
    assert response.status == "answered"
    assert response.columns == ["gender", "customer_count"]
    assert sorted(response.rows) == [["Female", 2], ["Male", 1]]
    assert response.sql and response.sql.startswith("SELECT")
    assert [item.agent for item in response.trace] == [
        "knowledge_retrieval", "query_planner", "sql_generation", "validation", "orchestrator"
    ]
    planning_prompt = provider.prompts[0][1]
    assert '"available_metadata"' in planning_prompt
    assert '"live_table_schema_count": 10' in planning_prompt
    assert '"id": "dataset.bank-workshop"' in planning_prompt
    assert '"id": "metric.customer-net-cash-flow"' in planning_prompt
    assert "account_id BIGINT NULL" in planning_prompt


@pytest.mark.parametrize(
    "question",
    [
        "What tables are available to query?",
        "What tables are avialable to query?",
        "Show all table schemas",
        "What can I query?",
        "What datasets and business rules are available?",
    ],
)
def test_chat_returns_all_live_schemas_and_semantic_objects_without_model(
    bank_database: Path, question: str
):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    response = ChatOrchestrator(
        bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), None
    ).chat(ChatRequest(message=question))

    assert response.status == "answered"
    assert response.answer == (
        "64 metadata objects are available to explore, including 10 live DuckDB "
        "table schemas and every object in the active semantic bundle."
    )
    assert response.columns == ["kind", "id", "name", "details"]
    assert response.row_count == 64
    assert response.truncated is False
    kinds = {row[0] for row in response.rows}
    assert kinds >= {"dataset", "physical_table", "metric", "business_rule"}
    accounts = next(row for row in response.rows if row[1] == "table.accounts")
    assert "account_id BIGINT" in accounts[3]
    customer_net_cash_flow = next(
        row for row in response.rows if row[1] == "metric.customer-net-cash-flow"
    )
    assert "SUM(CASE WHEN" in customer_net_cash_flow[3]
    assert response.evidence_ids == sorted(obj.id for obj in bundle.objects)
    assert [item.agent for item in response.trace] == [
        "catalog_inspection",
        "semantic_inventory",
        "orchestrator",
    ]
