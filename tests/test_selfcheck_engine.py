from __future__ import annotations

import duckdb
import pytest
import text2sql_factories as factories

from cerebro.executor import DuckDBExecutor, EngineValidator, Executor
from cerebro.models import BoundParameter, CompiledQuery
from cerebro.provenance import canonicalize_question
from cerebro.sql_compiler import DialectCompiler


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    """A schema that matches the shared factory snapshot, not the real corpus."""
    path = tmp_path_factory.mktemp("gate") / "gate.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE accounts ("
        "account_id BIGINT, branch_id BIGINT, city VARCHAR, "
        "customer_name VARCHAR, status VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
        [
            (
                index,
                index % 3,
                "London" if index % 2 else "Delhi",
                f"Name {index}",
                "OPEN",
            )
            for index in range(1, 13)
        ],
    )
    connection.close()
    return str(path)


def _compile(ir, snapshot, question, max_rows=10):
    canonical = canonicalize_question(question)
    validated = factories.validated_ir(ir, snapshot, canonical)
    return DialectCompiler("duckdb").compile(
        validated, snapshot, canonical, max_rows=max_rows
    )


# --- protocols -------------------------------------------------------------


def test_duckdb_executor_satisfies_both_protocols(database):
    executor = DuckDBExecutor(database)
    assert isinstance(executor, EngineValidator)
    assert isinstance(executor, Executor)
    executor.close()


def test_protocol_conforming_fake_is_accepted():
    class FakeEngine:
        def validate(self, compiled):
            return ()

        def execute(self, compiled, max_rows):  # pragma: no cover - shape only
            raise NotImplementedError

    fake = FakeEngine()
    assert isinstance(fake, EngineValidator)
    assert isinstance(fake, Executor)


# --- compiled queries reach the engine intact ------------------------------


def test_compiler_output_validates_and_executes(snapshot, database):
    compiled = _compile(
        factories.filtered_account_ir(snapshot), snapshot, "Show accounts in London"
    )
    executor = DuckDBExecutor(database)
    assert executor.validate(compiled) == ()
    result = executor.execute(compiled, max_rows=10)
    assert result.columns == ("account_id",)
    assert result.row_count == 6
    executor.close()


def test_sensitive_projection_respects_its_compiled_limit(snapshot, database):
    compiled = _compile(
        factories.sensitive_ir(snapshot),
        snapshot,
        "Which five customer names are in London",
        max_rows=1000,
    )
    executor = DuckDBExecutor(database)
    assert executor.validate(compiled) == ()
    result = executor.execute(compiled, max_rows=1000)
    assert result.row_count <= 5
    executor.close()


def test_every_executable_path_runs_explain_first(snapshot, database):
    """A binder failure must be reported before any row is fetched."""
    broken = CompiledQuery(
        sql="SELECT not_a_column FROM accounts",
        parameters=(),
        ir_hash="0" * 64,
        compiler_version="test",
        dialect="duckdb",
    )
    executor = DuckDBExecutor(database)
    violations = executor.validate(broken)
    assert [violation.code for violation in violations] == ["explain_failed"]
    executor.close()


def test_engine_violations_are_sanitized(snapshot, database):
    executor = DuckDBExecutor(database)
    violations = executor.validate(
        CompiledQuery(
            sql="SELECT * FROM secret_table",
            parameters=(),
            ir_hash="0" * 64,
            compiler_version="test",
            dialect="duckdb",
        )
    )
    assert violations
    serialized = violations[0].model_dump_json()
    assert "secret_table" not in serialized
    assert "Traceback" not in serialized
    executor.close()


def test_parameters_are_never_echoed_in_a_violation(snapshot, database):
    executor = DuckDBExecutor(database)
    violations = executor.validate(
        CompiledQuery(
            sql="SELECT not_a_column FROM accounts WHERE city = ?",
            parameters=(
                BoundParameter(position=1, data_type="string", value="CANARY_CITY"),
            ),
            ir_hash="0" * 64,
            compiler_version="test",
            dialect="duckdb",
        )
    )
    assert violations
    assert "CANARY_CITY" not in violations[0].model_dump_json()
    executor.close()
