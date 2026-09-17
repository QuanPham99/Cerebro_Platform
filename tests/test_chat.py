import asyncio
import logging
import threading
from pathlib import Path
from uuid import UUID, uuid4

import duckdb
import pytest
from fastapi import HTTPException

from cerebro.api import create_app
from cerebro.bundle import load_validated_bundle
from cerebro.chat import (
    ChatCancellation,
    ChatCancelled,
    ChatOrchestrator,
    ChatRequestRegistry,
    DuckDBQueryExecutor,
    DuplicateChatRequest,
    SQLGuardrail,
    SQLSafetyError,
)
from cerebro.enrichment import GenerationProvider
from cerebro.models import AnswerPayload, ChatRequest, ChatResponse, QueryPlanAndSQL, SQLProposal
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
        if output_model is QueryPlanAndSQL:
            return QueryPlanAndSQL(
                intent="Count customers by gender",
                tables=["customers"],
                group_by=["gender"],
                sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender",
                explanation="Safe aggregate",
            )
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


def test_sql_guardrail_allows_order_by_referencing_a_select_alias():
    # Regression for spec 015: a window-function column aliased in the SELECT list and referenced
    # by that alias in ORDER BY must validate, matching the real query the agent generated for
    # "Which branches have the highest fraud exposure, and what drives that metric?".
    guardrail = SQLGuardrail(load_validated_bundle(DEFAULT_BUNDLE))
    sql = (
        "SELECT b.branch_name, ct.merchant_category, c.card_type, "
        "SUM(CASE WHEN ct.is_fraud = 1 THEN ct.amount ELSE 0 END) AS fraud_exposure, "
        "SUM(SUM(CASE WHEN ct.is_fraud = 1 THEN ct.amount ELSE 0 END)) OVER (PARTITION BY b.branch_name) AS branch_fraud_exposure "
        'FROM "main"."branches" AS b '
        'JOIN "main"."accounts" AS a ON b.branch_id = a.branch_id '
        'JOIN "main"."cards" AS c ON a.account_id = c.account_id '
        'JOIN "main"."card_transactions" AS ct ON c.card_id = ct.card_id '
        "GROUP BY b.branch_name, ct.merchant_category, c.card_type "
        "ORDER BY branch_fraud_exposure DESC, fraud_exposure DESC"
    )
    validated = guardrail.validate(sql)
    assert "ORDER BY branch_fraud_exposure" in validated
    # A genuinely unresolvable bare reference is still rejected.
    with pytest.raises(SQLSafetyError, match="approved catalog"):
        guardrail.validate("SELECT COUNT(*) AS total FROM customers ORDER BY not_a_real_alias")


def test_suggest_indexes_emits_one_statement_per_governed_join_column():
    from cerebro.chat import suggest_indexes

    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    statements = suggest_indexes(bundle)
    assert statements == sorted(statements)
    assert 'CREATE INDEX IF NOT EXISTS "idx_accounts_customer_id" ON "main"."accounts" ("customer_id");' in statements
    assert 'CREATE INDEX IF NOT EXISTS "idx_customers_customer_id" ON "main"."customers" ("customer_id");' in statements
    # Never touches the database: statements are derived purely from bundle relationships.
    assert all(statement.startswith("CREATE INDEX IF NOT EXISTS") for statement in statements)


def test_chat_runs_validated_read_only_query(bank_database: Path, caplog: pytest.LogCaptureFixture):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    provider = ChatProvider()
    with caplog.at_level(logging.INFO, logger="uvicorn.error.cerebro.chat"):
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
    # Spec 025: the plan and SQL proposal are now one merged call, so a query-requiring
    # turn makes exactly 2 model calls total (merged query_plan, then database_answer)
    # instead of 3.
    assert [schema for schema, _ in provider.prompts] == ["query_plan", "database_answer"]
    planning_prompt = provider.prompts[0][1]
    assert '"available_metadata"' in planning_prompt
    assert '"live_table_schema_count": 10' in planning_prompt
    assert '"id": "dataset.bank-workshop"' in planning_prompt
    assert '"id": "metric.customer-net-cash-flow"' in planning_prompt
    assert "account_id BIGINT NULL" in planning_prompt
    assert "chat.llm.started" in caplog.text
    assert "stage=query_plan" in caplog.text
    assert "chat.llm.completed" in caplog.text
    assert '"answer": "There are two female customers and one male customer."' in caplog.text


def test_chat_cancellation_stops_after_an_in_flight_provider_call(bank_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    cancellation = ChatCancellation()

    class CancellingProvider(ChatProvider):
        def generate(self, schema_name, prompt, output_model):
            result = super().generate(schema_name, prompt, output_model)
            cancellation.cancel()
            return result

    provider = CancellingProvider()
    with pytest.raises(ChatCancelled):
        ChatOrchestrator(
            bundle,
            SemanticRetriever(bundle),
            bank_database,
            _settings(bank_database),
            provider,
        ).chat(ChatRequest(message="How many customers are there by gender?"), cancellation)

    assert [schema for schema, _ in provider.prompts] == ["query_plan"]


def test_chat_sql_repair_runs_once_after_guardrail_rejection(bank_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)

    class RepairingProvider(ChatProvider):
        def generate(self, schema_name, prompt, output_model):
            self.prompts.append((schema_name, prompt))
            if output_model is QueryPlanAndSQL:
                return QueryPlanAndSQL(
                    intent="Count customers by gender",
                    tables=["customers"],
                    group_by=["gender"],
                    sql="SELECT * FROM customers",
                    explanation="Draft",
                )
            if output_model is SQLProposal:
                return SQLProposal(
                    sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender",
                    explanation="Repaired",
                )
            if output_model is AnswerPayload:
                return AnswerPayload(answer="There are two female customers and one male customer.")
            raise AssertionError(schema_name)

    provider = RepairingProvider()
    response = ChatOrchestrator(
        bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), provider
    ).chat(ChatRequest(message="How many customers are there by gender?"))
    assert response.status == "answered"
    assert [schema for schema, _ in provider.prompts] == ["query_plan", "sql_repair", "database_answer"]


def test_chat_clarification_makes_exactly_one_model_call(bank_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)

    class ClarifyingProvider(ChatProvider):
        def generate(self, schema_name, prompt, output_model):
            self.prompts.append((schema_name, prompt))
            if output_model is QueryPlanAndSQL:
                return QueryPlanAndSQL(intent="Unclear", clarification="Which time period do you mean?")
            raise AssertionError(schema_name)

    provider = ClarifyingProvider()
    response = ChatOrchestrator(
        bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), provider
    ).chat(ChatRequest(message="How many are there recently?"))
    assert response.status == "clarification"
    assert [schema for schema, _ in provider.prompts] == ["query_plan"]


def test_chat_no_query_answer_skips_sql_generation_call(bank_database: Path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)

    class SemanticOnlyProvider(ChatProvider):
        def generate(self, schema_name, prompt, output_model):
            self.prompts.append((schema_name, prompt))
            if output_model is QueryPlanAndSQL:
                return QueryPlanAndSQL(intent="Explain a metric", requires_query=False)
            if output_model is AnswerPayload:
                return AnswerPayload(answer="Customer net cash flow is defined as inflows minus outflows.")
            raise AssertionError(schema_name)

    provider = SemanticOnlyProvider()
    response = ChatOrchestrator(
        bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), provider
    ).chat(ChatRequest(message="What does customer net cash flow mean?"))
    assert response.status == "answered"
    assert response.sql is None
    assert [schema for schema, _ in provider.prompts] == ["query_plan", "semantic_answer"]


def test_duckdb_query_executor_reuses_one_connection(bank_database: Path, monkeypatch):
    real_connect = duckdb.connect
    connect_calls: list[object] = []

    def counting_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr("cerebro.chat.duckdb.connect", counting_connect)
    executor = DuckDBQueryExecutor(bank_database)
    assert len(connect_calls) == 0  # opened lazily, not at construction
    executor.table_schemas()
    assert len(connect_calls) == 1
    executor.table_schemas()
    executor.execute("SELECT COUNT(*) AS total FROM customers")
    executor.execute("SELECT COUNT(*) AS total FROM accounts")
    assert len(connect_calls) == 1


def test_duckdb_query_executor_caches_table_schemas(bank_database: Path):
    executor = DuckDBQueryExecutor(bank_database)
    first = executor.table_schemas()
    second = executor.table_schemas()
    assert first is second


def test_duckdb_query_executor_recovers_after_engine_error(bank_database: Path):
    executor = DuckDBQueryExecutor(bank_database)
    executor.table_schemas()  # force the lazy connection open before swapping it out
    original_connection = executor._connection

    class FailingConnection:
        def execute(self, sql):
            raise duckdb.Error("boom")

        def interrupt(self):
            pass

        def close(self):
            pass

    executor._connection = FailingConnection()
    with pytest.raises(duckdb.Error):
        executor.execute("SELECT 1")
    assert executor._connection is not original_connection

    columns, rows, truncated = executor.execute("SELECT COUNT(*) AS total FROM customers")
    assert columns == ["total"]
    assert rows == [[3]]
    assert truncated is False


def test_duckdb_execution_is_interrupted_by_chat_cancellation(monkeypatch, tmp_path):
    started = threading.Event()
    interrupted = threading.Event()
    errors: list[BaseException] = []

    class BlockingConnection:
        description = [("total",)]

        def execute(self, sql):
            # Only the real query blocks; a real DuckDB connection applies a `SET`
            # tuning pragma synchronously/instantly, so the mock should too.
            if sql.strip().upper().startswith("SET "):
                return self
            started.set()
            interrupted.wait(2)
            raise duckdb.InterruptException("cancelled")

        def interrupt(self):
            interrupted.set()

        def close(self):
            pass

    monkeypatch.setattr("cerebro.chat.duckdb.connect", lambda *_args, **_kwargs: BlockingConnection())
    executor = DuckDBQueryExecutor(tmp_path / "unused.duckdb", timeout_seconds=5)
    cancellation = ChatCancellation()

    def run() -> None:
        try:
            executor.execute("SELECT COUNT(*) AS total FROM large_table", cancellation)
        except BaseException as exc:  # noqa: BLE001 - captured for the worker assertion
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(1)
    cancellation.cancel()
    worker.join(1)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ChatCancelled)


def test_chat_request_registry_handles_duplicates_and_pre_cancellation():
    registry = ChatRequestRegistry()
    request_id = str(uuid4())
    cancellation = registry.register(request_id)
    with pytest.raises(DuplicateChatRequest):
        registry.register(request_id)
    registry.cancel(request_id)
    with pytest.raises(ChatCancelled):
        cancellation.checkpoint()
    registry.finish(request_id, cancellation)

    pre_cancelled_id = str(uuid4())
    registry.cancel(pre_cancelled_id)
    pre_cancelled = registry.register(pre_cancelled_id)
    with pytest.raises(ChatCancelled):
        pre_cancelled.checkpoint()


def test_chat_cancel_endpoint_remains_available_while_chat_runs(
    bank_source_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    started = threading.Event()

    def blocking_chat(self, request, cancellation=None):
        assert cancellation is not None
        started.set()
        while not cancellation.cancelled:
            threading.Event().wait(0.01)
        cancellation.checkpoint()

    monkeypatch.setattr(ChatOrchestrator, "chat", blocking_chat)
    app = create_app(
        source_config=bank_source_config,
        generation_output_root=tmp_path / "generated",
    )
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if hasattr(route, "endpoint")
    }
    chat_endpoint = endpoints["/api/chat"]
    cancel_endpoint = endpoints["/api/chat/requests/{request_id}/cancel"]

    async def exercise() -> None:
        request_id = str(uuid4())
        running = asyncio.create_task(chat_endpoint(ChatRequest(
            message="customer count", request_id=request_id
        )))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        cancelled = await cancel_endpoint(UUID(request_id))
        assert cancelled == {
            "request_id": request_id,
            "status": "cancellation_requested",
        }
        done, _ = await asyncio.wait({running}, timeout=1)
        assert running in done
        with pytest.raises(HTTPException) as response:
            await running
        assert response.value.status_code == 409
        assert response.value.detail["code"] == "chat_cancelled"

    asyncio.run(exercise())


def test_chat_endpoint_logs_user_message_and_response_without_raw_rows(
    bank_source_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    def successful_chat(self, request, cancellation=None):
        return ChatResponse(
            conversation_id="conversation-logged",
            status="answered",
            answer="Logged answer",
            sql="SELECT gender, COUNT(*) FROM customers GROUP BY gender",
            columns=["gender", "customer_count"],
            rows=[["Female", 2]],
            row_count=1,
            semantic_version="0.2.0",
        )

    monkeypatch.setattr(ChatOrchestrator, "chat", successful_chat)
    app = create_app(
        source_config=bank_source_config,
        generation_output_root=tmp_path / "generated",
    )
    chat_endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/chat"
    )
    request_id = uuid4()

    with caplog.at_level(logging.INFO, logger="uvicorn.error.cerebro.chat"):
        response = asyncio.run(chat_endpoint(ChatRequest(
            message="Có bao nhiêu khách hàng theo từng giới tính?",
            request_id=request_id,
        )))

    assert response.status == "answered"
    assert f'"request_id": "{request_id}"' in caplog.text
    assert '"message": "Có bao nhiêu khách hàng theo từng giới tính?"' in caplog.text
    assert '"answer": "Logged answer"' in caplog.text
    assert '"rows_omitted": 1' in caplog.text
    assert "Female" not in caplog.text


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
        "75 metadata objects are available to explore, including 10 live DuckDB "
        "table schemas and every object in the active semantic bundle."
    )
    assert response.columns == ["kind", "id", "name", "details"]
    assert response.row_count == 75
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
