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

    def generate(self, schema_name, prompt, output_model):
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
    response = ChatOrchestrator(
        bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), ChatProvider()
    ).chat(ChatRequest(message="How many customers are there by gender?"))
    assert response.status == "answered"
    assert response.columns == ["gender", "customer_count"]
    assert sorted(response.rows) == [["Female", 2], ["Male", 1]]
    assert response.sql and response.sql.startswith("SELECT")
    assert [item.agent for item in response.trace] == [
        "knowledge_retrieval", "query_planner", "sql_generation", "validation", "orchestrator"
    ]
