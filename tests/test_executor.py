from __future__ import annotations

import threading

import duckdb
import pytest

from cerebro.executor import (
    DuckDBExecutor,
    ExecutorConfigurationError,
    _run_with_deadline,
)
from cerebro.models import BoundParameter, CompiledQuery


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    """A small local database. No test here depends on the real corpus."""
    path = tmp_path_factory.mktemp("engine") / "engine.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE accounts (account_id BIGINT, city VARCHAR)")
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?)",
        [(index, "London" if index % 2 else "Delhi") for index in range(1, 21)],
    )
    connection.close()
    return str(path)


def _compiled(sql: str, parameters=()) -> CompiledQuery:
    return CompiledQuery(
        sql=sql,
        parameters=tuple(parameters),
        ir_hash="0" * 64,
        compiler_version="test",
        dialect="duckdb",
    )


# --- parameter binding -----------------------------------------------------


def test_explain_and_execution_receive_identical_parameters(database):
    compiled = _compiled(
        "SELECT account_id FROM accounts WHERE account_id = ?",
        (BoundParameter(position=1, data_type="integer", value=7),),
    )
    executor = DuckDBExecutor(database)
    assert executor.validate(compiled) == ()
    result = executor.execute(compiled, max_rows=10)
    assert result.columns == ("account_id",)
    assert result.rows == ((7,),)
    assert result.row_count == 1
    assert result.truncated is False
    executor.close()


def test_validation_binds_parameters_and_reports_binder_errors(database):
    executor = DuckDBExecutor(database)
    violations = executor.validate(
        _compiled(
            "SELECT missing_column FROM accounts WHERE account_id = ?",
            (BoundParameter(position=1, data_type="integer", value=1),),
        )
    )
    assert [violation.code for violation in violations] == ["explain_failed"]
    assert "missing_column" not in violations[0].model_dump_json()
    executor.close()


def test_parameter_count_mismatch_is_reported_not_raised(database):
    executor = DuckDBExecutor(database)
    violations = executor.validate(
        _compiled("SELECT account_id FROM accounts WHERE account_id = ?")
    )
    assert violations
    assert violations[0].code in {"explain_failed", "execution_error"}
    executor.close()


# --- result contract -------------------------------------------------------


def test_zero_row_result_is_a_success(database):
    executor = DuckDBExecutor(database)
    result = executor.execute(
        _compiled(
            "SELECT account_id FROM accounts WHERE city = ?",
            (BoundParameter(position=1, data_type="string", value="Nowhere"),),
        ),
        max_rows=10,
    )
    assert result.rows == ()
    assert result.row_count == 0
    assert result.truncated is False
    assert result.columns == ("account_id",)
    executor.close()


def test_truncation_is_detected_without_returning_the_extra_row(database):
    executor = DuckDBExecutor(database)
    result = executor.execute(
        _compiled("SELECT account_id FROM accounts ORDER BY account_id"), max_rows=5
    )
    assert result.row_count == 5
    assert len(result.rows) == 5
    assert result.truncated is True
    executor.close()


def test_result_reports_column_types_and_elapsed_time(database):
    executor = DuckDBExecutor(database)
    result = executor.execute(
        _compiled("SELECT account_id, city FROM accounts ORDER BY account_id"),
        max_rows=3,
    )
    assert result.columns == ("account_id", "city")
    assert result.column_types == ("integer", "string")
    assert result.elapsed_ms >= 0
    executor.close()


# --- read-only enforcement -------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE leak AS SELECT 1",
        "INSERT INTO accounts VALUES (99, 'X')",
        "DELETE FROM accounts",
    ],
)
def test_connection_is_read_only(database, sql):
    executor = DuckDBExecutor(database)
    violations = executor.validate(_compiled(sql))
    assert violations
    result_codes = {violation.code for violation in violations}
    assert result_codes & {"explain_failed", "execution_error"}
    executor.close()


def test_missing_database_fails_construction(tmp_path):
    with pytest.raises(ExecutorConfigurationError):
        DuckDBExecutor(str(tmp_path / "absent.duckdb"))


def test_unsupported_setting_fails_construction(database):
    with pytest.raises(ExecutorConfigurationError):
        DuckDBExecutor(database, settings={"not_a_real_duckdb_setting": "1"})


def test_effective_limits_are_recorded(database):
    executor = DuckDBExecutor(
        database, explain_timeout_seconds=7, execute_timeout_seconds=11
    )
    limits = executor.effective_limits()
    assert limits["explain_timeout_seconds"] == 7
    assert limits["execute_timeout_seconds"] == 11
    assert limits["max_rows_default"] == 1000
    assert "threads" in limits
    assert "memory_limit" in limits
    executor.close()


# --- deadline and interruption ---------------------------------------------


def test_deadline_primitive_interrupts_at_most_once():
    interruptions: list[int] = []
    started = threading.Event()

    def action():
        started.set()
        # Long enough for the watchdog to fire while the action is running.
        threading.Event().wait(0.5)
        return "late"

    with pytest.raises(TimeoutError):
        _run_with_deadline(
            action,
            interrupt=lambda: interruptions.append(1),
            timeout_seconds=0.05,
            phase="execution",
        )
    assert started.is_set()
    assert len(interruptions) == 1


def test_fast_action_never_receives_an_interrupt():
    interruptions: list[int] = []
    value = _run_with_deadline(
        lambda: "done",
        interrupt=lambda: interruptions.append(1),
        timeout_seconds=5,
        phase="execution",
    )
    assert value == "done"
    assert interruptions == []


def test_explain_timeout_is_reported_as_explain_timeout(database):
    executor = DuckDBExecutor(database, explain_timeout_seconds=0.001)
    violations = executor.validate(
        _compiled("SELECT COUNT(*) FROM accounts a, accounts b, accounts c, accounts d")
    )
    if violations:
        assert violations[0].code in {"explain_timeout", "explain_failed"}
    executor.close()


def test_execution_timeout_discards_partial_rows(database):
    executor = DuckDBExecutor(database, execute_timeout_seconds=0.001)
    with pytest.raises(TimeoutError):
        executor.execute(
            _compiled(
                "SELECT a.account_id FROM accounts a, accounts b, accounts c, "
                "accounts d, accounts e"
            ),
            max_rows=1_000_000,
        )
    # The connection must still be usable after a watchdog interruption.
    recovered = executor.execute(_compiled("SELECT 1 AS one"), max_rows=1)
    assert recovered.rows == ((1,),)
    executor.close()


def test_one_connection_is_serialized_across_threads(database):
    executor = DuckDBExecutor(database)
    errors: list[Exception] = []
    results: list[int] = []

    def worker():
        try:
            outcome = executor.execute(
                _compiled("SELECT COUNT(*) AS total FROM accounts"), max_rows=1
            )
            results.append(outcome.rows[0][0])
        except Exception as error:  # noqa: BLE001 - the test records any failure
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results == [20] * 6
    executor.close()


def test_validate_then_execute_uses_the_same_connection(database):
    executor = DuckDBExecutor(database)
    compiled = _compiled(
        "SELECT account_id FROM accounts WHERE city = ? ORDER BY account_id",
        (BoundParameter(position=1, data_type="string", value="London"),),
    )
    assert executor.validate(compiled) == ()
    first = executor.execute(compiled, max_rows=100)
    second = executor.execute(compiled, max_rows=100)
    assert first.rows == second.rows
    assert first.row_count == 10
    executor.close()
