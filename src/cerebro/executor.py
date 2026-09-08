"""Mandatory parameter-aware `EXPLAIN` and bounded read-only execution.

One connection, one lock, and one watchdog per phase. The watchdog sets its
fired flag before interrupting, so a deadline wins a simultaneous completion
race, and the lock is held through the join so a later query never reuses a
connection that is still being interrupted.
"""

from __future__ import annotations

import threading
import time
from datetime import date as _Date
from datetime import datetime as _DateTime
from datetime import time as _Time
from datetime import timedelta as _TimeDelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import duckdb

from .models import CheckViolation, CompiledQuery, QueryResult

DEFAULT_EXPLAIN_TIMEOUT_SECONDS = 30
DEFAULT_EXECUTE_TIMEOUT_SECONDS = 30
DEFAULT_MAX_ROWS = 1000

_DUCKDB_TYPE_TO_SCALAR = {
    "BIGINT": "integer",
    "HUGEINT": "integer",
    "INTEGER": "integer",
    "SMALLINT": "integer",
    "TINYINT": "integer",
    "UBIGINT": "integer",
    "UINTEGER": "integer",
    "USMALLINT": "integer",
    "UTINYINT": "integer",
    "NUMBER": "integer",
    "DOUBLE": "decimal",
    "FLOAT": "decimal",
    "REAL": "decimal",
    "DECIMAL": "decimal",
    "VARCHAR": "string",
    "STRING": "string",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "DATETIME": "timestamp",
}


class ExecutorConfigurationError(Exception):
    """Raised when the engine cannot be constructed with required settings."""


@runtime_checkable
class EngineValidator(Protocol):
    def validate(self, compiled: CompiledQuery) -> tuple[CheckViolation, ...]: ...


@runtime_checkable
class Executor(Protocol):
    def execute(self, compiled: CompiledQuery, max_rows: int) -> QueryResult: ...


def _run_with_deadline(
    action: Any,
    interrupt: Any,
    timeout_seconds: float,
    phase: str,
) -> Any:
    """Run one action under a single-shot watchdog. The only such primitive."""
    completed = threading.Event()
    fired = threading.Event()
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = action()
        except BaseException as error:  # noqa: BLE001 - re-raised on the caller
            outcome["error"] = error
        finally:
            completed.set()

    thread = threading.Thread(target=worker, name=f"cerebro-{phase}", daemon=True)
    thread.start()
    if not completed.wait(timeout_seconds):
        # Set `fired` before interrupting so a simultaneous completion cannot
        # make an expired deadline look like a success.
        fired.set()
        interrupt()
    # Unconditional join: the caller keeps owning the connection until the
    # worker has actually stopped touching it.
    thread.join()

    if fired.is_set():
        raise TimeoutError(f"{phase} exceeded its deadline")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _scalar_type(duckdb_type: str) -> str:
    key = str(duckdb_type).upper().split("(", 1)[0]
    return _DUCKDB_TYPE_TO_SCALAR.get(key, "string")


def _cell(value: Any) -> Any:
    """Render one engine cell as a JSON scalar without changing its meaning.

    Temporal and exact-decimal cells arrive as Python objects the response
    contract cannot carry. They are rendered losslessly and canonically rather
    than dropped, because a silently absent cell would misreport the result.
    Non-finite floats are deliberately not rewritten: the contract rejects them,
    and inventing a substitute would hide an engine-level anomaly.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_DateTime, _Date, _Time)):
        return value.isoformat()
    if isinstance(value, _TimeDelta):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


class DuckDBExecutor:
    """One serialized read-only DuckDB connection for validation and execution."""

    def __init__(
        self,
        database_path: str,
        *,
        explain_timeout_seconds: float = DEFAULT_EXPLAIN_TIMEOUT_SECONDS,
        execute_timeout_seconds: float = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
        max_rows_default: int = DEFAULT_MAX_ROWS,
        settings: dict[str, Any] | None = None,
    ) -> None:
        path = Path(database_path)
        if not path.is_file():
            raise ExecutorConfigurationError("database file is not present")
        self._path = str(path)
        self._explain_timeout = explain_timeout_seconds
        self._execute_timeout = execute_timeout_seconds
        self._max_rows_default = max_rows_default
        self._lock = threading.Lock()
        try:
            self._connection = duckdb.connect(self._path, read_only=True)
        except duckdb.Error as error:
            raise ExecutorConfigurationError(
                "cannot open a read-only connection"
            ) from error

        self._settings: dict[str, Any] = {
            "threads": "4",
            "memory_limit": "1GB",
            **(settings or {}),
        }
        for name, value in self._settings.items():
            try:
                self._connection.execute(f"SET {name}='{value}'")
            except duckdb.Error as error:
                self._connection.close()
                raise ExecutorConfigurationError(
                    f"required setting is unsupported: {name}"
                ) from error

    def effective_limits(self) -> dict[str, Any]:
        """Report the effective bounds this engine actually enforces."""
        return {
            "explain_timeout_seconds": self._explain_timeout,
            "execute_timeout_seconds": self._execute_timeout,
            "max_rows_default": self._max_rows_default,
            "read_only": True,
            **self._settings,
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _violation(code: str) -> CheckViolation:
        # Only the stable code crosses this boundary: an engine message can quote
        # schema names or parameter content.
        return CheckViolation(code=code, stage="engine_validation", subject_ids=())

    def _recover_locked(self) -> None:
        """Replace the connection after an interruption, under the held lock."""
        try:
            self._connection.close()
        except duckdb.Error:
            pass
        self._connection = duckdb.connect(self._path, read_only=True)
        for name, value in self._settings.items():
            try:
                self._connection.execute(f"SET {name}='{value}'")
            except duckdb.Error:
                pass

    def validate(self, compiled: CompiledQuery) -> tuple[CheckViolation, ...]:
        """Run parameter-aware `EXPLAIN`. Failures are sanitized, never raised."""
        parameters = [item.value for item in compiled.parameters]
        with self._lock:
            try:
                _run_with_deadline(
                    lambda: self._connection.execute(
                        f"EXPLAIN {compiled.sql}", parameters
                    ).fetchall(),
                    interrupt=self._connection.interrupt,
                    timeout_seconds=self._explain_timeout,
                    phase="engine_validation",
                )
            except TimeoutError:
                self._recover_locked()
                return (self._violation("explain_timeout"),)
            except duckdb.Error:
                self._recover_locked()
                return (self._violation("explain_failed"),)
            except Exception:  # noqa: BLE001 - any other fault stays local
                self._recover_locked()
                return (self._violation("execution_error"),)
        return ()

    def execute(self, compiled: CompiledQuery, max_rows: int) -> QueryResult:
        """Execute under one deadline covering both execution and fetch."""
        if max_rows <= 0:
            raise ExecutorConfigurationError("max_rows must be positive")
        parameters = [item.value for item in compiled.parameters]
        started = time.monotonic()

        def action() -> tuple[list[Any], list[tuple[Any, ...]]]:
            cursor = self._connection.execute(compiled.sql, parameters)
            description = list(cursor.description or [])
            # One extra row detects truncation; it is never returned.
            rows = cursor.fetchmany(max_rows + 1)
            return description, rows

        with self._lock:
            try:
                description, rows = _run_with_deadline(
                    action,
                    interrupt=self._connection.interrupt,
                    timeout_seconds=self._execute_timeout,
                    phase="execution",
                )
            except TimeoutError:
                # Partial rows are discarded with the connection.
                self._recover_locked()
                raise
            except duckdb.Error:
                self._recover_locked()
                raise

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        truncated = len(rows) > max_rows
        returned = rows[:max_rows]
        return QueryResult(
            columns=tuple(item[0] for item in description),
            column_types=tuple(_scalar_type(item[1]) for item in description),
            rows=tuple(tuple(_cell(cell) for cell in row) for row in returned),
            row_count=len(returned),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )
