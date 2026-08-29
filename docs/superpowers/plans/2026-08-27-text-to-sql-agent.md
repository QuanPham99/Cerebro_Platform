# Text-to-SQL Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an agent that turns a banking question plus an OKF grounding packet into a verified `QueryPlan`, dialect-correct DuckDB SQL, and an executed aggregate result, where every check that gates execution is deterministic and runs without a model. Generation runs against a hosted provider using an organizer-supplied API key.

**Architecture:** Two structured stages behind the existing `GenerationProvider` interface. Stage one produces a `QueryPlan`; stage two produces SQL. Between and after them, a model-free checker (`selfcheck.py`) verifies containment against the grounding packet, warning coverage, statement shape, verbatim metric formula by AST comparison, bounded sensitive disclosure, and engine validity via `EXPLAIN`. Execution is injected as a callable so the agent is testable without a database. Retry budgets are per-stage and bounded.

**Tech Stack:** Python 3.10+, Pydantic v2, DuckDB (read-only at query time), `sqlglot` for AST work, `httpx` against an OpenAI-compatible chat-completions endpoint, `pytest`. No new package is added; `httpx` is already a project dependency.

**Spec:** [`specs/008-text-to-sql-agent.md`](../../../specs/008-text-to-sql-agent.md)

## Global Constraints

- Generation goes through a hosted provider with an organizer-supplied key, read from the environment and never committed (FR-702).
- Both stages bind the output schema at the API level rather than asking the model to behave (FR-703).
- Prompt egress is bounded: no source row reaches the provider, enforced at a single choke point (FR-703a).
- Transport faults are retried with bounded backoff and never consume the semantic retry budget (FR-703b).
- The whole suite runs with no key and no network, replaying recorded responses (FR-703c, AC-707).
- Temperature is zero for both stages; reasoning modes are off where the provider exposes the choice.
- The key is shared and quota-bearing. Iterating on the checker must not require live calls.
- `sqlglot` is the only SQL parser. No regular-expression SQL analysis (spec Constraints).
- All DuckDB connections used for query execution are opened read-only (FR-718).
- `knowledge/bank-workshop/` is read-only input. No task modifies the bundle.
- Module layout stays flat under `src/cerebro/`. No subpackages.
- Classification is read from the grounding packet at runtime, never hard-coded.
- Disclosure cap default is 50 rows; row cap default 1000; statement timeout default 30 s. All configuration, not literals (FR-713, FR-719).
- `status = ok` is the only status that may carry a result, and the only one a caller may execute (FR-706, FR-717).

## File Structure

| File | Responsibility | Model? |
|---|---|---|
| `scripts/load_duckdb.py` | Build DuckDB from CSVs using DDL derived from the bundle | no |
| `src/cerebro/models.py` (modify) | Contract types shared by agent, checker, callers | no |
| `src/cerebro/selfcheck.py` | Every deterministic gate: containment, warnings, shape, formula, disclosure, engine | no |
| `src/cerebro/hosted_provider.py` | Hosted `GenerationProvider`, egress guard, cassette replay, scripted double | no |
| `src/cerebro/text2sql.py` | Two stages, retry budgets, status assembly | yes |
| `src/cerebro/executor.py` | Read-only DuckDB execution with row cap and timeout | no |
| `src/cerebro/cli.py` (modify) | `cerebro ask` and `cerebro baseline` | no |
| `src/cerebro/evaluation.py` (modify) | Golden-set harness producing the baseline artifact | no |

Everything except `text2sql.py` is model-free, and `text2sql.py` is tested against the scripted double. Every task in this plan is therefore testable with no key and no network; exactly two steps in Task 6 make a live call, and both are one-off confirmations.

---

### Task 1: Materialize DuckDB from bundle-derived DDL

Implements FR-700, FR-701. Verifies T-700.

**Files:**
- Create: `scripts/load_duckdb.py`
- Create: `tests/test_load_duckdb.py`
- Modify: `config/bank-source.yaml` (the `database_path` value only)

**Interfaces:**
- Consumes: `cerebro.bundle.load_validated_bundle`, `cerebro.models.SemanticBundle`
- Produces:
  - `ddl_from_bundle(bundle: SemanticBundle) -> dict[str, str]`
  - `columns_from_bundle(bundle: SemanticBundle) -> dict[str, list[str]]`
  - `load_csvs(csv_dir: Path, db_path: Path, bundle: SemanticBundle) -> dict[str, int]`
  - `class LoadError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_duckdb.py`:

```python
from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.paths import DEFAULT_BUNDLE
from scripts.load_duckdb import LoadError, columns_from_bundle, ddl_from_bundle, load_csvs

SAMPLE = {
    "BIGINT": "1",
    "VARCHAR": "x",
    "DOUBLE": "1.5",
    "DATE": "2020-01-01",
}


def _write_fixture_csvs(directory: Path, bundle) -> None:
    """One header row plus one data row per table, driven by the bundle itself."""
    for table, names in columns_from_bundle(bundle).items():
        declared = {
            column["name"]: column["data_type"]
            for obj in bundle.objects
            if obj.id == f"table.{table}"
            for column in obj.cerebro["columns"]
        }
        path = directory / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            writer.writerow([SAMPLE[declared[name]] for name in names])


def test_ddl_covers_every_declared_table_and_column():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    ddl = ddl_from_bundle(bundle)
    assert len(ddl) == 10
    assert sum(len(names) for names in columns_from_bundle(bundle).values()) == 75


def test_load_applies_declared_types_not_inferred_types(tmp_path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    _write_fixture_csvs(tmp_path, bundle)
    db_path = tmp_path / "workshop.duckdb"

    counts = load_csvs(tmp_path, db_path, bundle)

    assert counts["transactions"] == 1
    connection = duckdb.connect(str(db_path), read_only=True)
    actual = dict(
        connection.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'transactions'"
        ).fetchall()
    )
    connection.close()
    assert actual["txn_date"] == "DATE"
    assert actual["amount"] == "DOUBLE"


def test_header_divergence_fails_closed(tmp_path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    _write_fixture_csvs(tmp_path, bundle)
    path = tmp_path / "transactions.csv"
    rows = list(csv.reader(path.open(newline="", encoding="utf-8")))
    rows[0][0] = "renamed_column"
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    with pytest.raises(LoadError, match="transactions"):
        load_csvs(tmp_path, tmp_path / "bad.duckdb", bundle)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_load_duckdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts'`

- [ ] **Step 3: Make `scripts` importable**

Create `scripts/__init__.py` as an empty file, and add `scripts` to the test path so the module resolves. Modify `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "."]
```

- [ ] **Step 4: Write minimal implementation**

Create `scripts/load_duckdb.py`:

```python
"""Build the demo DuckDB from CSVs using DDL derived from the OKF bundle.

Build-time tooling. The bundle is the single source of column names, order, and
types, so the physical schema cannot drift from the semantic contract.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb

from cerebro.bundle import load_validated_bundle
from cerebro.models import SemanticBundle
from cerebro.paths import DEFAULT_BUNDLE, ROOT


class LoadError(RuntimeError):
    """Raised when source CSVs diverge from the bundle declaration."""


def _tables(bundle: SemanticBundle):
    for obj in sorted(bundle.objects, key=lambda item: item.id):
        if obj.type == "table":
            yield obj.id.split(".", 1)[1], obj.cerebro.get("columns", [])


def columns_from_bundle(bundle: SemanticBundle) -> dict[str, list[str]]:
    return {table: [column["name"] for column in columns] for table, columns in _tables(bundle)}


def ddl_from_bundle(bundle: SemanticBundle) -> dict[str, str]:
    statements = {}
    for table, columns in _tables(bundle):
        fields = ", ".join(f'"{column["name"]}" {column["data_type"]}' for column in columns)
        statements[table] = f'CREATE TABLE "{table}" ({fields})'
    return statements


def load_csvs(csv_dir: Path, db_path: Path, bundle: SemanticBundle) -> dict[str, int]:
    expected = columns_from_bundle(bundle)
    statements = ddl_from_bundle(bundle)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    connection = duckdb.connect(str(db_path))
    try:
        for table, statement in statements.items():
            source = Path(csv_dir) / f"{table}.csv"
            if not source.is_file():
                raise LoadError(f"{table}: missing source file {source}")
            with source.open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle), [])
            if header != expected[table]:
                raise LoadError(
                    f"{table}: CSV header does not match bundle declaration; "
                    f"expected {expected[table]}, found {header}"
                )
            connection.execute(f'DROP TABLE IF EXISTS "{table}"')
            connection.execute(statement)
            connection.execute(
                f'INSERT INTO "{table}" '
                "SELECT * FROM read_csv(?, header = true, all_varchar = true)",
                [str(source)],
            )
            counts[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        connection.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the demo DuckDB from CSVs")
    parser.add_argument("--csv-dir", type=Path, default=ROOT / "archive")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "workshop.duckdb")
    args = parser.parse_args(argv)
    counts = load_csvs(args.csv_dir, args.database, load_validated_bundle(DEFAULT_BUNDLE))
    for table, rows in counts.items():
        print(f"{table:<20} {rows:>10,} rows")
    print(f"total{' ' * 15} {sum(counts.values()):>10,} rows -> {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Reading every column as `VARCHAR` and letting `INSERT` cast into the declared
types is deliberate: it makes the bundle's declaration authoritative rather
than DuckDB's inference, which is the whole point of FR-700.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_load_duckdb.py -v`
Expected: 3 passed

- [ ] **Step 6: Load the real data and confirm counts**

Run: `python scripts/load_duckdb.py`
Expected output ends with `total 5,868,950 rows`, made up of:

```
accounts             95,000     branches            150      cards              65,000
card_transactions 3,000,000     customers        60,000      employees           1,800
loan_payments       600,000     loans            22,000      support_tickets    25,000
transactions      2,000,000
```

If any count differs, stop: the CSVs are not the set this plan was written against.

- [ ] **Step 7: Point the config at the local database**

Modify `config/bank-source.yaml`, changing only the `database_path` value to `data/workshop.duckdb`. Leave `expected_table_count`, `expected_column_count`, relationships, and rules untouched.

- [ ] **Step 8: Verify the existing scan contract still holds**

Run: `python -m pytest tests/ -v`
Expected: all tests pass, including the previously unrunnable `test_upstream_and_source.py` which needs a real database.

- [ ] **Step 9: Commit**

```bash
git add scripts/__init__.py scripts/load_duckdb.py tests/test_load_duckdb.py pyproject.toml config/bank-source.yaml
git commit -m "feat: materialize DuckDB from bundle-derived DDL"
```

---

### Task 2: Contract types

Implements FR-704 to FR-708 type surface. Verifies T-702.

**Files:**
- Modify: `src/cerebro/models.py` (append after `GroundingResponse`)
- Create: `tests/test_text2sql_contract.py`

**Interfaces:**
- Consumes: `GroundingResponse` from Task 0 baseline (already in the repo)
- Produces: `QueryPlan`, `CheckViolation`, `ViolationCode`, `QueryResult`, `SQLGenerationRequest`, `SQLGenerationResponse`

- [ ] **Step 1: Write the failing test**

Create `tests/test_text2sql_contract.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from cerebro.models import (
    CheckViolation,
    QueryPlan,
    QueryResult,
    SQLGenerationRequest,
    SQLGenerationResponse,
)


def _plan() -> QueryPlan:
    return QueryPlan(
        intent="fraud rate by card type",
        grain="one row per card type",
        tables=["table.card_transactions", "table.cards"],
        columns=["card_type", "is_fraud"],
        metric_ids=["metric.card-fraud-rate"],
        joins=["relationship.card_transaction_card"],
        group_by=["card_type"],
        warnings_addressed=["Use card transaction grain; do not mix directly with account transactions."],
    )


def test_ok_response_carries_plan_and_result():
    response = SQLGenerationResponse(
        status="ok",
        semantic_version="0.1.0",
        dialect="duckdb",
        sql="SELECT 1",
        plan=_plan(),
        result=QueryResult(
            columns=["card_type"],
            column_types=["VARCHAR"],
            rows=[["Debit"]],
            row_count=1,
            truncated=False,
            elapsed_ms=3,
        ),
        used_grounding_ids=["table.cards"],
    )
    assert response.result.row_count == 1
    assert response.violations == []


def test_refused_response_names_unmet_needs_and_emits_no_sql():
    response = SQLGenerationResponse(
        status="refused",
        semantic_version="0.1.0",
        dialect="duckdb",
        unmet_needs=["no ATM entity in the bundle"],
    )
    assert response.sql == ""
    assert response.plan is None
    assert response.result is None


def test_check_failed_response_carries_violations():
    response = SQLGenerationResponse(
        status="check_failed",
        semantic_version="0.1.0",
        dialect="duckdb",
        sql="SELECT * FROM atms",
        violations=[CheckViolation(code="unknown_table", message="atms", subject="atms")],
    )
    assert response.violations[0].code == "unknown_table"
    assert response.result is None


def test_unknown_violation_code_is_rejected():
    with pytest.raises(ValidationError):
        CheckViolation(code="totally_made_up", message="nope")


def test_request_defaults_to_duckdb_and_a_row_cap():
    request = SQLGenerationRequest.model_construct(question="q", grounding=None)
    assert request.dialect == "duckdb"
    assert request.max_rows == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_text2sql_contract.py -v`
Expected: FAIL with `ImportError: cannot import name 'CheckViolation'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/cerebro/models.py`:

```python
ViolationCode = Literal[
    "unknown_table",
    "unknown_column",
    "undeclared_join",
    "unknown_metric",
    "formula_not_verbatim",
    "unaddressed_warning",
    "unbounded_sensitive_projection",
    "non_select_statement",
    "explain_failed",
    "execution_error",
    "unparsable_sql",
    "unparsable_plan",
]


class CheckViolation(BaseModel):
    code: ViolationCode
    message: str
    subject: str = ""


class QueryPlan(BaseModel):
    intent: str
    grain: str
    tables: list[str]
    columns: list[str]
    metric_ids: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    row_limit: int | None = None
    warnings_addressed: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class SQLGenerationRequest(BaseModel):
    question: str
    grounding: GroundingResponse
    dialect: Literal["duckdb"] = "duckdb"
    max_rows: int = 1000


class SQLGenerationResponse(BaseModel):
    status: Literal["ok", "check_failed", "refused"]
    semantic_version: str
    dialect: str
    sql: str = ""
    plan: QueryPlan | None = None
    result: QueryResult | None = None
    violations: list[CheckViolation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unmet_needs: list[str] = Field(default_factory=list)
    used_grounding_ids: list[str] = Field(default_factory=list)
    attempts: int = 1
    provider: str = ""
    model: str = ""
```

`Any` and `Literal` are already imported at the top of `models.py`; no import change is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_text2sql_contract.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/cerebro/models.py tests/test_text2sql_contract.py
git commit -m "feat: add text-to-sql contract types"
```

---

### Task 3: Plan containment and warning coverage checks

Implements FR-709, FR-710. Verifies T-703, T-704.

**Files:**
- Create: `src/cerebro/selfcheck.py`
- Create: `tests/test_selfcheck_plan.py`

**Interfaces:**
- Consumes: `QueryPlan`, `CheckViolation`, `GroundingResponse` from Task 2
- Produces:
  - `check_plan(plan: QueryPlan, grounding: GroundingResponse) -> list[CheckViolation]`
  - `grounding_index(grounding: GroundingResponse) -> GroundingIndex`
  - `class GroundingIndex` with attributes `tables: set[str]`, `columns: set[str]`, `joins: set[str]`, `metrics: set[str]`, `warnings_by_object: dict[str, list[str]]`, `sensitive: dict[str, str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_selfcheck_plan.py`:

```python
from __future__ import annotations

import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.models import QueryPlan
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.retrieval import SemanticRetriever
from cerebro.selfcheck import check_plan, grounding_index


@pytest.fixture(scope="module")
def grounding():
    retriever = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    return retriever.grounding("what is the fraud rate by card type")


def _valid_plan(grounding) -> QueryPlan:
    index = grounding_index(grounding)
    warnings = index.warnings_by_object["table.card_transactions"] + index.warnings_by_object["metric.card-fraud-rate"]
    return QueryPlan(
        intent="fraud rate by card type",
        grain="one row per card type",
        tables=["table.card_transactions", "table.cards"],
        columns=["is_fraud", "card_type"],
        metric_ids=["metric.card-fraud-rate"],
        joins=["relationship.card_transaction_card"],
        group_by=["card_type"],
        warnings_addressed=warnings,
    )


def test_valid_plan_has_no_violations(grounding):
    assert check_plan(_valid_plan(grounding), grounding) == []


@pytest.mark.parametrize(
    "field, value, code",
    [
        ("tables", ["table.atms"], "unknown_table"),
        ("columns", ["atm_serial"], "unknown_column"),
        ("joins", ["relationship.card_atm"], "undeclared_join"),
        ("metric_ids", ["metric.atm-uptime"], "unknown_metric"),
    ],
)
def test_absent_objects_raise_matching_codes(grounding, field, value, code):
    plan = _valid_plan(grounding).model_copy(update={field: value})
    codes = {violation.code for violation in check_plan(plan, grounding)}
    assert code in codes


def test_missing_warning_coverage_is_reported(grounding):
    plan = _valid_plan(grounding).model_copy(update={"warnings_addressed": []})
    violations = check_plan(plan, grounding)
    assert any(violation.code == "unaddressed_warning" for violation in violations)


def test_warning_violation_names_the_owning_object(grounding):
    plan = _valid_plan(grounding).model_copy(update={"warnings_addressed": []})
    subjects = {v.subject for v in check_plan(plan, grounding) if v.code == "unaddressed_warning"}
    assert "table.card_transactions" in subjects
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_selfcheck_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebro.selfcheck'`

- [ ] **Step 3: Write minimal implementation**

Create `src/cerebro/selfcheck.py`:

```python
"""Deterministic gates between the model and the database.

No function in this module calls a model. Every check is a pure function of a
plan, a SQL string, or a grounding packet, so the whole gate is testable with
fixtures and no provider running.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CheckViolation, GroundingResponse, QueryPlan

SENSITIVE_TIERS = ("restricted", "confidential")


@dataclass
class GroundingIndex:
    tables: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)
    joins: set[str] = field(default_factory=set)
    metrics: set[str] = field(default_factory=set)
    warnings_by_object: dict[str, list[str]] = field(default_factory=dict)
    sensitive: dict[str, str] = field(default_factory=dict)


def grounding_index(grounding: GroundingResponse) -> GroundingIndex:
    index = GroundingIndex()
    for entry in grounding.tables:
        index.tables.add(entry["id"])
        cerebro = entry.get("cerebro", {})
        index.warnings_by_object[entry["id"]] = [str(w) for w in cerebro.get("warnings", [])]
        for column in cerebro.get("columns", []):
            index.columns.add(column["name"])
            if column.get("classification") in SENSITIVE_TIERS:
                index.sensitive[column["name"]] = column["classification"]
    for entry in grounding.joins:
        index.joins.add(entry["id"])
        index.warnings_by_object[entry["id"]] = [str(w) for w in entry.get("warnings", [])]
    for entry in grounding.metrics:
        index.metrics.add(entry["id"])
        index.warnings_by_object[entry["id"]] = [str(w) for w in entry.get("warnings", [])]
    for entry in grounding.concepts:
        cerebro = entry.get("cerebro", {})
        index.warnings_by_object[entry["id"]] = [str(w) for w in cerebro.get("warnings", [])]
    return index


def check_plan(plan: QueryPlan, grounding: GroundingResponse) -> list[CheckViolation]:
    index = grounding_index(grounding)
    violations: list[CheckViolation] = []

    for name, declared, allowed, code in (
        ("tables", plan.tables, index.tables, "unknown_table"),
        ("columns", plan.columns, index.columns, "unknown_column"),
        ("joins", plan.joins, index.joins, "undeclared_join"),
        ("metric_ids", plan.metric_ids, index.metrics, "unknown_metric"),
    ):
        for value in declared:
            if value not in allowed:
                violations.append(
                    CheckViolation(
                        code=code,
                        message=f"plan.{name} references {value!r}, absent from the grounding packet",
                        subject=value,
                    )
                )

    addressed = set(plan.warnings_addressed)
    for object_id in list(plan.tables) + list(plan.joins) + list(plan.metric_ids):
        for warning in index.warnings_by_object.get(object_id, []):
            if warning not in addressed:
                violations.append(
                    CheckViolation(
                        code="unaddressed_warning",
                        message=f"{object_id} carries an unaddressed warning: {warning}",
                        subject=object_id,
                    )
                )
    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_selfcheck_plan.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/cerebro/selfcheck.py tests/test_selfcheck_plan.py
git commit -m "feat: add plan containment and warning coverage checks"
```

---

### Task 4: Statement shape, verbatim formula, and bounded disclosure

Implements FR-711, FR-712, FR-713. Verifies T-705, T-706, T-707.

**Files:**
- Modify: `src/cerebro/selfcheck.py` (append)
- Modify: `pyproject.toml` (add `sqlglot`)
- Create: `tests/test_selfcheck_sql.py`

**Interfaces:**
- Consumes: `grounding_index`, `GroundingIndex` from Task 3
- Produces:
  - `check_sql(sql: str, plan: QueryPlan, grounding: GroundingResponse, disclosure_cap: int = 50) -> list[CheckViolation]`
  - `normalize_expression(expression_sql: str) -> str`

- [ ] **Step 1: Add the parser dependency**

Modify `pyproject.toml`, inserting `"sqlglot>=25.0",` into `dependencies` in alphabetical position after `"pyyaml>=6.0",`. Then run:

```bash
python -m pip install -e '.[dev]'
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_selfcheck_sql.py`:

```python
from __future__ import annotations

import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.models import QueryPlan
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.retrieval import SemanticRetriever
from cerebro.selfcheck import check_sql

FORMULA = "100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0)"


@pytest.fixture(scope="module")
def grounding():
    return SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE)).grounding("what is the fraud rate by card type")


@pytest.fixture
def plan():
    return QueryPlan(
        intent="fraud rate by card type",
        grain="one row per card type",
        tables=["table.card_transactions"],
        columns=["is_fraud"],
        metric_ids=["metric.card-fraud-rate"],
    )


def _codes(sql, plan, grounding, **kwargs):
    return {v.code for v in check_sql(sql, plan, grounding, **kwargs)}


def test_unaliased_formula_passes(plan, grounding):
    sql = f"SELECT {FORMULA} AS fraud_rate FROM card_transactions"
    assert "formula_not_verbatim" not in _codes(sql, plan, grounding)


def test_aliased_table_still_matches_by_ast(plan, grounding):
    sql = "SELECT 100.0 * SUM(ct.is_fraud) / NULLIF(COUNT(*), 0) AS r FROM card_transactions AS ct"
    assert "formula_not_verbatim" not in _codes(sql, plan, grounding)


def test_altered_formula_is_rejected(plan, grounding):
    sql = "SELECT 100.0 * SUM(ct.is_fraud) / COUNT(*) AS r FROM card_transactions AS ct"
    assert "formula_not_verbatim" in _codes(sql, plan, grounding)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "DROP TABLE customers",
        "UPDATE customers SET name = 'x'",
        "PRAGMA database_list",
    ],
)
def test_non_select_and_multi_statement_rejected(sql, plan, grounding):
    assert "non_select_statement" in _codes(sql, plan, grounding)


def test_unparsable_sql_is_reported(plan, grounding):
    assert "unparsable_sql" in _codes("SELECT FROM WHERE ((", plan, grounding)


def test_restricted_column_needs_a_bounded_limit(grounding):
    bare = QueryPlan(intent="i", grain="g", tables=["table.customers"], columns=["name"])
    accepted = "SELECT name FROM customers ORDER BY annual_income DESC LIMIT 5"
    unbounded = "SELECT name FROM customers"
    too_wide = "SELECT name FROM customers LIMIT 1000"

    assert "unbounded_sensitive_projection" not in _codes(accepted, bare, grounding)
    assert "unbounded_sensitive_projection" in _codes(unbounded, bare, grounding)
    assert "unbounded_sensitive_projection" in _codes(too_wide, bare, grounding)


def test_aggregated_confidential_column_needs_no_limit(grounding):
    bare = QueryPlan(intent="i", grain="g", tables=["table.customers"], columns=["annual_income"])
    sql = "SELECT AVG(annual_income) AS mean_income FROM customers"
    assert "unbounded_sensitive_projection" not in _codes(sql, bare, grounding)
```

One fixture is enough for all of these. The packet retrieved for the card
question has been verified to contain eight tables including `table.customers`,
so `name` (restricted) and `annual_income` (confidential) are both in its
sensitive set. One-hop graph expansion is why the packet is broader than the
question: `concept.card-fraud` and `concept.active-customer` pull customer
tables in. Do not narrow the fixture to make the test simpler; the breadth is
the real runtime condition.

Note that `check_sql` deliberately does not verify that a projected column
belongs to a table in the plan. That containment is Task 3's job (FR-709), and
duplicating it here would put the same rule in two places.

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_selfcheck_sql.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_sql'`

- [ ] **Step 4: Write minimal implementation**

Append to `src/cerebro/selfcheck.py`:

```python
import sqlglot
from sqlglot import exp

DIALECT = "duckdb"


def normalize_expression(expression_sql: str) -> str:
    """Render an expression with table qualifiers stripped, so aliases cannot defeat comparison."""
    tree = sqlglot.parse_one(expression_sql, read=DIALECT)
    for column in tree.find_all(exp.Column):
        column.set("table", None)
    return tree.sql(dialect=DIALECT).lower()


def _projections(statement: exp.Expression):
    for select in statement.find_all(exp.Select):
        for projection in select.expressions:
            yield projection.this if isinstance(projection, exp.Alias) else projection


def _row_limit(statement: exp.Expression) -> int | None:
    limit = statement.args.get("limit")
    if limit is None:
        return None
    try:
        return int(limit.expression.name)
    except (AttributeError, ValueError):
        return None


def check_sql(
    sql: str,
    plan: QueryPlan,
    grounding: GroundingResponse,
    disclosure_cap: int = 50,
) -> list[CheckViolation]:
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except Exception as exc:
        return [CheckViolation(code="unparsable_sql", message=str(exc))]
    statements = [item for item in statements if item is not None]
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return [
            CheckViolation(
                code="non_select_statement",
                message="exactly one SELECT statement is required",
            )
        ]

    statement = statements[0]
    index = grounding_index(grounding)
    violations: list[CheckViolation] = []

    rendered = {normalize_expression(node.sql(dialect=DIALECT)) for node in _projections(statement)}
    formulas = {entry["id"]: entry.get("formula") for entry in grounding.metrics}
    for metric_id in plan.metric_ids:
        formula = formulas.get(metric_id)
        if not formula:
            continue
        if normalize_expression(formula) not in rendered:
            violations.append(
                CheckViolation(
                    code="formula_not_verbatim",
                    message=f"governed formula for {metric_id} is absent from the projection",
                    subject=metric_id,
                )
            )

    disclosed = set()
    for node in _projections(statement):
        if isinstance(node, exp.AggFunc) or node.find(exp.AggFunc) is not None:
            continue
        for column in node.find_all(exp.Column):
            if column.name in index.sensitive:
                disclosed.add(column.name)
    if disclosed:
        limit = _row_limit(statement)
        if limit is None or limit > disclosure_cap:
            violations.append(
                CheckViolation(
                    code="unbounded_sensitive_projection",
                    message=(
                        f"projection discloses {sorted(disclosed)} with "
                        f"{'no row limit' if limit is None else f'limit {limit}'}; "
                        f"cap is {disclosure_cap}"
                    ),
                    subject=",".join(sorted(disclosed)),
                )
            )
    return violations
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_selfcheck_sql.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/cerebro/selfcheck.py tests/test_selfcheck_sql.py
git commit -m "feat: add statement shape, formula AST, and disclosure checks"
```

---

### Task 5: Engine validation before execution

Implements FR-714. Verifies T-709.

**Files:**
- Modify: `src/cerebro/selfcheck.py` (append)
- Create: `tests/test_selfcheck_engine.py`

**Interfaces:**
- Produces: `check_engine(sql: str, connection) -> list[CheckViolation]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_selfcheck_engine.py`:

```python
from __future__ import annotations

import duckdb
import pytest

from cerebro.selfcheck import check_engine


@pytest.fixture
def connection():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (a BIGINT, b VARCHAR)")
    con.execute("INSERT INTO t VALUES (1, 'x')")
    yield con
    con.close()


def test_valid_sql_passes(connection):
    assert check_engine("SELECT a FROM t", connection) == []


def test_unknown_column_reports_explain_failed(connection):
    violations = check_engine("SELECT nope FROM t", connection)
    assert [v.code for v in violations] == ["explain_failed"]


def test_unknown_table_reports_explain_failed(connection):
    assert [v.code for v in check_engine("SELECT a FROM missing", connection)] == ["explain_failed"]


def test_explain_returns_no_rows_from_the_table(connection):
    check_engine("SELECT a FROM t", connection)
    assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_selfcheck_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_engine'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/cerebro/selfcheck.py`:

```python
def check_engine(sql: str, connection) -> list[CheckViolation]:
    """Ask the engine to bind the query without returning data."""
    try:
        connection.execute(f"EXPLAIN {sql}")
    except Exception as exc:
        return [CheckViolation(code="explain_failed", message=str(exc).strip().splitlines()[0])]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_selfcheck_engine.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cerebro/selfcheck.py tests/test_selfcheck_engine.py
git commit -m "feat: validate SQL against the engine before execution"
```

---

### Task 6: Hosted provider, transport policy, and offline replay

Implements FR-702, FR-703, FR-703a, FR-703b, FR-703c. Verifies T-701, T-718, T-719, T-720.

**Files:**
- Create: `src/cerebro/hosted_provider.py`
- Create: `tests/test_hosted_provider.py`
- Modify: `.env.example` (create if absent)

**Interfaces:**
- Consumes: `GenerationProvider`, `OutputT` from `cerebro.enrichment`
- Produces:
  - `class HostedProvider(GenerationProvider)` with `name = "hosted"`, `generate(schema_name, prompt, output_model)`
  - `class ProviderUnavailable(RuntimeError)`
  - `class ScriptedProvider(GenerationProvider)` taking `responses: list[BaseModel | Exception]`, exposing `calls: list[tuple[str, str]]`
  - `class EgressGuard(GenerationProvider)` wrapping a provider with a forbidden-substring policy
  - `class CassetteProvider(GenerationProvider)` recording to and replaying from a JSONL fixture
  - `provider_from_environment() -> GenerationProvider | None`

**Assumption to confirm before starting:** the organizer key is used against an
OpenAI-compatible `/chat/completions` endpoint supporting
`response_format={"type": "json_schema", ...}`. That covers OpenAI, Azure,
OpenRouter, Groq, Together, and vLLM gateways. If the organizers hand you a
native Anthropic or Gemini endpoint instead, only `HostedProvider.generate`
changes; every other class, every test, and every later task stay as written.
Record whichever you got in `config/provider.yaml` and note it in the task log.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hosted_provider.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from cerebro.hosted_provider import (
    CassetteProvider,
    EgressGuard,
    HostedProvider,
    ProviderUnavailable,
    ScriptedProvider,
)
from cerebro.models import QueryPlan

PLAN = QueryPlan(intent="i", grain="g", tables=["table.cards"], columns=["card_type"])


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://provider.example/v1",
    )


def _ok(content: str):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_scripted_provider_returns_queued_outputs_and_records_calls():
    provider = ScriptedProvider([PLAN])
    assert provider.generate("query_plan", "prompt text", QueryPlan).tables == ["table.cards"]
    assert provider.calls == [("query_plan", "prompt text")]


def test_scripted_provider_raises_queued_exceptions():
    with pytest.raises(ValueError):
        ScriptedProvider([ValueError("bad json")]).generate("query_plan", "p", QueryPlan)


def test_hosted_provider_binds_schema_and_pins_temperature():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["auth"] = request.headers.get("authorization")
        return _ok(PLAN.model_dump_json())

    provider = HostedProvider(model="m", api_key="secret-key", client=_client(handler))
    assert provider.generate("query_plan", "prompt", QueryPlan).tables == ["table.cards"]

    assert captured["temperature"] == 0
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["response_format"]["json_schema"]["schema"] == QueryPlan.model_json_schema()
    assert captured["auth"] == "Bearer secret-key"


def test_hosted_provider_surfaces_schema_invalid_output():
    provider = HostedProvider(
        model="m", api_key="k", client=_client(lambda r: _ok('{"intent": "only"}'))
    )
    with pytest.raises(ValueError, match="query_plan"):
        provider.generate("query_plan", "prompt", QueryPlan)


def test_secret_never_appears_in_error_text():
    provider = HostedProvider(
        model="m", api_key="super-secret", client=_client(lambda r: httpx.Response(400, text="bad"))
    )
    with pytest.raises(Exception) as caught:
        provider.generate("query_plan", "prompt", QueryPlan)
    assert "super-secret" not in str(caught.value)
    assert "super-secret" not in repr(provider)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transport_faults_retry_then_succeed(status):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(status, text="try later")
        return _ok(PLAN.model_dump_json())

    slept: list[float] = []
    provider = HostedProvider(
        model="m", api_key="k", client=_client(handler), sleep=slept.append, max_transport_attempts=3
    )
    assert provider.generate("query_plan", "p", QueryPlan).tables == ["table.cards"]
    assert calls["n"] == 2
    assert slept and slept[0] > 0


def test_exhausted_transport_retries_raise_provider_unavailable():
    slept: list[float] = []
    provider = HostedProvider(
        model="m",
        api_key="k",
        client=_client(lambda r: httpx.Response(503, text="down")),
        sleep=slept.append,
        max_transport_attempts=3,
    )
    with pytest.raises(ProviderUnavailable):
        provider.generate("query_plan", "p", QueryPlan)
    assert len(slept) == 2


def test_backoff_grows_between_attempts():
    slept: list[float] = []
    provider = HostedProvider(
        model="m",
        api_key="k",
        client=_client(lambda r: httpx.Response(503, text="down")),
        sleep=slept.append,
        max_transport_attempts=4,
    )
    with pytest.raises(ProviderUnavailable):
        provider.generate("query_plan", "p", QueryPlan)
    assert slept == sorted(slept) and slept[0] < slept[-1]


def test_egress_guard_blocks_forbidden_values_before_any_call():
    inner = ScriptedProvider([PLAN])
    guarded = EgressGuard(inner, forbidden=["Pooja Garcia", "customer0@mailbank.com"])

    with pytest.raises(ValueError, match="egress"):
        guarded.generate("query_plan", "top customer is Pooja Garcia", QueryPlan)
    assert inner.calls == []

    guarded.generate("query_plan", "fraud rate by card type", QueryPlan)
    assert len(inner.calls) == 1


def test_cassette_records_then_replays_without_the_network(tmp_path: Path):
    path = tmp_path / "cassette.jsonl"
    live = ScriptedProvider([PLAN])

    recorder = CassetteProvider(path, inner=live)
    recorder.generate("query_plan", "prompt", QueryPlan)

    replay = CassetteProvider(path, inner=None)
    assert replay.generate("query_plan", "prompt", QueryPlan).tables == ["table.cards"]


def test_replay_fails_loudly_on_a_missing_fixture(tmp_path: Path):
    replay = CassetteProvider(tmp_path / "empty.jsonl", inner=None)
    with pytest.raises(ProviderUnavailable, match="no recorded response"):
        replay.generate("query_plan", "never recorded", QueryPlan)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hosted_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebro.hosted_provider'`

- [ ] **Step 3: Write minimal implementation**

Create `src/cerebro/hosted_provider.py`:

```python
"""Generation providers reached over the network, plus the doubles that keep tests offline.

Four classes, one job each:

`HostedProvider`  talks to an OpenAI-compatible endpoint, binds the output schema
                  at the API level, and separates transport faults from model faults.
`EgressGuard`     is the single choke point every prompt passes through. Because
                  inference is remote, FR-703a is enforced here rather than trusted
                  to each call site.
`CassetteProvider` records live responses once and replays them forever, so the
                  suite needs neither a key nor a network (FR-703c).
`ScriptedProvider` is the in-test double for agent logic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterable

import httpx
from pydantic import BaseModel, ValidationError

from .enrichment import GenerationProvider, OutputT

DEFAULT_BASE_URL = "https://api.openai.com/v1"
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
SYSTEM_PROMPT = (
    "You are a bounded banking query agent. Use only the objects supplied in the "
    "grounding packet. Never invent a table, column, join, or metric. Reply with "
    "JSON matching the provided schema and nothing else."
)


class ProviderUnavailable(RuntimeError):
    """Transport could not be completed within the retry budget."""


class HostedProvider(GenerationProvider):
    name = "hosted"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
        max_transport_attempts: int = 4,
        backoff_base: float = 0.5,
        sleep: Callable[[float], None] | None = None,
    ):
        self.model = model
        self._key = api_key
        self.max_transport_attempts = max_transport_attempts
        self.backoff_base = backoff_base
        self._sleep = sleep or __import__("time").sleep
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def __repr__(self) -> str:  # keep the key out of tracebacks and logs
        return f"HostedProvider(model={self.model!r}, key=***)"

    def _payload(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> dict:
        return {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": output_model.model_json_schema(),
                },
            },
        }

    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        payload = self._payload(schema_name, prompt, output_model)
        headers = {"authorization": f"Bearer {self._key}"}
        last = ""
        for attempt in range(1, self.max_transport_attempts + 1):
            try:
                response = self.client.post("/chat/completions", json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = type(exc).__name__
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    if response.status_code >= 400:
                        raise RuntimeError(
                            f"{schema_name}: provider rejected the request with "
                            f"status {response.status_code}"
                        )
                    content = response.json()["choices"][0]["message"]["content"]
                    try:
                        return output_model.model_validate_json(content)
                    except ValidationError as exc:
                        raise ValueError(f"{schema_name}: schema-invalid model output") from exc
                last = f"status {response.status_code}"
            if attempt < self.max_transport_attempts:
                self._sleep(self.backoff_base * (2 ** (attempt - 1)))
        raise ProviderUnavailable(f"{schema_name}: provider unreachable after {self.max_transport_attempts} attempts ({last})")


class EgressGuard(GenerationProvider):
    """Refuses to send a prompt containing a forbidden substring. FR-703a."""

    def __init__(self, inner: GenerationProvider, forbidden: Iterable[str]):
        self.inner = inner
        self.forbidden = [str(value) for value in forbidden if str(value).strip()]

    @property
    def name(self) -> str:
        return f"guarded:{self.inner.name}"

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", "")

    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        for value in self.forbidden:
            if value in prompt:
                raise ValueError(
                    f"{schema_name}: egress blocked; prompt carries a source value"
                )
        return self.inner.generate(schema_name, prompt, output_model)


class CassetteProvider(GenerationProvider):
    """Records responses when `inner` is set, replays from disk when it is not. FR-703c."""

    name = "cassette"

    def __init__(self, path: Path, inner: GenerationProvider | None):
        self.path = Path(path)
        self.inner = inner
        self.model = getattr(inner, "model", "replay")
        self._entries: dict[str, str] = {}
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._entries[entry["key"]] = entry["content"]

    @staticmethod
    def _key(schema_name: str, prompt: str) -> str:
        import hashlib

        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:32]
        return f"{schema_name}:{digest}"

    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        key = self._key(schema_name, prompt)
        if key in self._entries:
            return output_model.model_validate_json(self._entries[key])
        if self.inner is None:
            raise ProviderUnavailable(
                f"{schema_name}: no recorded response for this prompt; "
                "re-record the cassette with a live key"
            )
        result = self.inner.generate(schema_name, prompt, output_model)
        content = result.model_dump_json()
        self._entries[key] = content
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "content": content}) + "\n")
        return result


class ScriptedProvider(GenerationProvider):
    """Test double. Returns queued outputs in order and records every call."""

    name = "scripted"

    def __init__(self, responses: list[BaseModel | Exception], model: str = "scripted"):
        self.model = model
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        self.calls.append((schema_name, prompt))
        if not self._responses:
            raise AssertionError(f"ScriptedProvider exhausted on {schema_name}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider_from_environment() -> GenerationProvider | None:
    """Resolve a provider from configuration, or None so callers can fall back."""
    key = os.getenv("CEREBRO_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("CEREBRO_MODEL", "")
    if not key or not model:
        return None
    return HostedProvider(
        model=model,
        api_key=key,
        base_url=os.getenv("CEREBRO_BASE_URL", DEFAULT_BASE_URL),
    )
```

Two details worth naming. `EgressGuard` is a separate class rather than a flag
inside `HostedProvider` so that the guard can wrap the cassette and the scripted
double too, which is what makes T-718 meaningful. And `CassetteProvider` keys on
a hash of the prompt, so any prompt change invalidates the fixture rather than
silently replaying a stale answer.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hosted_provider.py -v`
Expected: 13 passed

- [ ] **Step 5: Record the credential shape without committing a secret**

Create `.env.example`:

```bash
# Supplied by the hackathon organizers. Never commit the real value.
CEREBRO_API_KEY=
CEREBRO_MODEL=
CEREBRO_BASE_URL=https://api.openai.com/v1
```

Confirm `.env` is already ignored, which it is, and confirm the key is absent
from tracked files:

```bash
git check-ignore -v .env
git grep -nE "sk-[A-Za-z0-9]{16,}" -- . ':!*.example' || echo "no key material tracked"
```

- [ ] **Step 6: Confirm the live path once, cheaply**

With the key exported, make exactly one live call so the assumption in this
task's header is confirmed rather than assumed:

```bash
python -c "
from cerebro.hosted_provider import provider_from_environment
from cerebro.models import QueryPlan
p = provider_from_environment()
print(p.name, p.model)
print(p.generate('query_plan', 'Return a plan with tables=[\'table.cards\'] and columns=[\'card_type\'].', QueryPlan))
"
```

Expected: a schema-valid `QueryPlan`. If the endpoint rejects
`response_format.json_schema`, stop and record which mechanism it does support;
that is the one place this plan's assumption can break, and it changes only
`HostedProvider._payload`.

- [ ] **Step 7: Prove the suite is offline and key-free (AC-707, T-720)**

```bash
env -u CEREBRO_API_KEY -u OPENAI_API_KEY python -m pytest tests/ -v
```

Expected: green. No test may reach the network. If any test hangs, it is calling
live rather than replaying, which is the defect T-720 exists to catch.

- [ ] **Step 8: Commit**

```bash
git add src/cerebro/hosted_provider.py tests/test_hosted_provider.py .env.example
git commit -m "feat: add hosted provider with schema binding, egress guard, and offline replay"
```

---

### Task 7: Agent with two stages, retry budgets, and refusal

Implements FR-704 to FR-708, FR-715 to FR-717. Verifies T-702, T-708, T-710, T-715, T-716.

**Files:**
- Create: `src/cerebro/text2sql.py`
- Create: `tests/test_text2sql_agent.py`

**Interfaces:**
- Consumes: `check_plan`, `check_sql`, `check_engine` from Tasks 3 to 5; `ScriptedProvider` from Task 6
- Produces:
  - `class SQLDraft(BaseModel)` with field `sql: str`
  - `class Text2SQLAgent` constructed as `Text2SQLAgent(provider, execute=None, connection=None, disclosure_cap=50, max_attempts=2)`
  - `Text2SQLAgent.run(request: SQLGenerationRequest) -> SQLGenerationResponse`
  - `Executor = Callable[[str, int], QueryResult]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_text2sql_agent.py`:

```python
from __future__ import annotations

import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.hosted_provider import ScriptedProvider
from cerebro.models import QueryPlan, QueryResult, SQLGenerationRequest
from cerebro.retrieval import SemanticRetriever
from cerebro.selfcheck import grounding_index
from cerebro.text2sql import SQLDraft, Text2SQLAgent

FORMULA = "100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0)"
GOOD_SQL = (
    f"SELECT c.card_type, {FORMULA} AS fraud_rate "
    "FROM card_transactions AS card_transactions "
    "JOIN cards AS c ON card_transactions.card_id = c.card_id "
    "GROUP BY c.card_type"
)


@pytest.fixture(scope="module")
def grounding():
    return SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE)).grounding("what is the fraud rate by card type")


@pytest.fixture
def request_obj(grounding):
    return SQLGenerationRequest(question="What is the fraud rate by card type?", grounding=grounding)


def _plan(grounding) -> QueryPlan:
    index = grounding_index(grounding)
    warnings = (
        index.warnings_by_object["table.card_transactions"]
        + index.warnings_by_object["table.cards"]
        + index.warnings_by_object["metric.card-fraud-rate"]
        + index.warnings_by_object["relationship.card_transaction_card"]
    )
    return QueryPlan(
        intent="fraud rate by card type",
        grain="one row per card type",
        tables=["table.card_transactions", "table.cards"],
        columns=["is_fraud", "card_type", "card_id"],
        metric_ids=["metric.card-fraud-rate"],
        joins=["relationship.card_transaction_card"],
        group_by=["card_type"],
        warnings_addressed=warnings,
    )


def _result() -> QueryResult:
    return QueryResult(
        columns=["card_type", "fraud_rate"],
        column_types=["VARCHAR", "DOUBLE"],
        rows=[["Debit", 0.4972]],
        row_count=1,
        truncated=False,
        elapsed_ms=12,
    )


def _agent(provider, execute=None):
    return Text2SQLAgent(provider, execute=execute or (lambda sql, cap: _result()))


def test_happy_path_returns_ok_with_plan_result_and_provenance(request_obj, grounding):
    provider = ScriptedProvider([_plan(grounding), SQLDraft(sql=GOOD_SQL)])
    response = _agent(provider).run(request_obj)

    assert response.status == "ok"
    assert response.violations == []
    assert response.result.row_count == 1
    assert response.semantic_version == grounding.semantic_version
    assert "table.card_transactions" in response.used_grounding_ids
    assert response.provider == "scripted"


def test_two_stages_are_called_in_order(request_obj, grounding):
    provider = ScriptedProvider([_plan(grounding), SQLDraft(sql=GOOD_SQL)])
    _agent(provider).run(request_obj)
    assert [name for name, _ in provider.calls] == ["query_plan", "sql_draft"]


def test_plan_violation_retries_then_fails_closed(request_obj, grounding):
    bad = _plan(grounding).model_copy(update={"tables": ["table.atms"]})
    provider = ScriptedProvider([bad, bad])
    response = _agent(provider).run(request_obj)

    assert response.status == "check_failed"
    assert response.attempts == 2
    assert any(v.code == "unknown_table" for v in response.violations)
    assert response.result is None


def test_sql_violation_returns_sql_but_no_result(request_obj, grounding):
    altered = GOOD_SQL.replace("NULLIF(COUNT(*), 0)", "COUNT(*)")
    provider = ScriptedProvider([_plan(grounding), SQLDraft(sql=altered), SQLDraft(sql=altered)])
    response = _agent(provider).run(request_obj)

    assert response.status == "check_failed"
    assert response.sql == altered
    assert response.result is None
    assert any(v.code == "formula_not_verbatim" for v in response.violations)


def test_empty_grounding_refuses_without_calling_the_model(grounding):
    empty = grounding.model_copy(update={"tables": [], "joins": [], "metrics": [], "concepts": []})
    provider = ScriptedProvider([])
    response = _agent(provider).run(SQLGenerationRequest(question="how many ATMs?", grounding=empty))

    assert response.status == "refused"
    assert response.unmet_needs
    assert response.sql == ""
    assert provider.calls == []


def test_zero_rows_is_success_and_is_not_retried(request_obj, grounding):
    empty_result = _result().model_copy(update={"rows": [], "row_count": 0})
    calls = []

    def execute(sql, cap):
        calls.append(sql)
        return empty_result

    provider = ScriptedProvider([_plan(grounding), SQLDraft(sql=GOOD_SQL)])
    response = Text2SQLAgent(provider, execute=execute).run(request_obj)

    assert response.status == "ok"
    assert response.result.row_count == 0
    assert len(calls) == 1


def test_schema_invalid_plan_output_is_recorded_as_a_violation(request_obj, grounding):
    provider = ScriptedProvider([ValueError("bad"), ValueError("bad")])
    response = _agent(provider).run(request_obj)

    assert response.status == "check_failed"
    assert any(v.code == "unparsable_plan" for v in response.violations)


def test_determinism_across_two_identical_runs(request_obj, grounding):
    first = _agent(ScriptedProvider([_plan(grounding), SQLDraft(sql=GOOD_SQL)])).run(request_obj)
    second = _agent(ScriptedProvider([_plan(grounding), SQLDraft(sql=GOOD_SQL)])).run(request_obj)
    assert first.sql == second.sql
    assert first.plan == second.plan
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_text2sql_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebro.text2sql'`

- [ ] **Step 3: Write minimal implementation**

Create `src/cerebro/text2sql.py`:

```python
"""Two-stage grounded text-to-SQL agent.

The model proposes; deterministic checks decide. Execution is injected so the
agent is testable without a database, and so the checker stays the only thing
standing between a model and the data.
"""

from __future__ import annotations

import json
from typing import Callable

from pydantic import BaseModel, ValidationError

from .enrichment import GenerationProvider
from .models import (
    CheckViolation,
    GroundingResponse,
    QueryPlan,
    QueryResult,
    SQLGenerationRequest,
    SQLGenerationResponse,
)
from .selfcheck import check_engine, check_plan, check_sql, grounding_index

Executor = Callable[[str, int], QueryResult]

UNGOVERNED_DIRECTION_NOTE = (
    "Transaction direction is not declared in the bundle. Treated as inflow: "
    "Deposit, Interest Credit, Transfer In. Treated as outflow: Withdrawal, "
    "Transfer Out, Fee Debit. This mapping is inferred, not governed."
)
DIRECTION_WORDS = ("outflow", "inflow", "withdraw", "spent", "spending", "deposit", "money out", "money in")


class SQLDraft(BaseModel):
    sql: str


class Text2SQLAgent:
    def __init__(
        self,
        provider: GenerationProvider,
        execute: Executor | None = None,
        connection=None,
        disclosure_cap: int = 50,
        max_attempts: int = 2,
    ):
        self.provider = provider
        self.execute = execute
        self.connection = connection
        self.disclosure_cap = disclosure_cap
        self.max_attempts = max_attempts

    # ---- prompts -------------------------------------------------------

    @staticmethod
    def _packet(grounding: GroundingResponse) -> str:
        return json.dumps(grounding.model_dump(mode="json"), indent=2, sort_keys=True)

    def _plan_prompt(self, request: SQLGenerationRequest, violations: list[CheckViolation]) -> str:
        parts = [
            "Stage 1 — produce a QueryPlan for this question using only the grounding packet.",
            "Every warning attached to an object you use must be copied into warnings_addressed.",
            "Reference joins by their relationship id, never by writing a predicate.",
            f"Question: {request.question}",
            f"Grounding packet:\n{self._packet(request.grounding)}",
        ]
        if violations:
            parts.append("Your previous plan was rejected:\n" + self._render(violations))
        return "\n\n".join(parts)

    def _sql_prompt(
        self,
        request: SQLGenerationRequest,
        plan: QueryPlan,
        violations: list[CheckViolation],
    ) -> str:
        parts = [
            f"Stage 2 — write one {request.dialect} SELECT statement implementing this plan.",
            "Embed each governed metric formula exactly as written in the packet.",
            "Anchor relative time windows on MAX of the time column, never CURRENT_DATE.",
            f"Question: {request.question}",
            f"Plan:\n{plan.model_dump_json(indent=2)}",
            f"Grounding packet:\n{self._packet(request.grounding)}",
        ]
        if violations:
            parts.append("Your previous SQL was rejected:\n" + self._render(violations))
        return "\n\n".join(parts)

    @staticmethod
    def _render(violations: list[CheckViolation]) -> str:
        return "\n".join(f"- [{v.code}] {v.message}" for v in violations)

    # ---- stages --------------------------------------------------------

    def _refusal(self, request: SQLGenerationRequest, needs: list[str]) -> SQLGenerationResponse:
        return SQLGenerationResponse(
            status="refused",
            semantic_version=request.grounding.semantic_version,
            dialect=request.dialect,
            unmet_needs=needs,
            provider=self.provider.name,
            model=self.provider.model,
        )

    def _assumptions(self, request: SQLGenerationRequest, plan: QueryPlan) -> list[str]:
        asked = request.question.lower()
        touches_transactions = "table.transactions" in plan.tables
        if touches_transactions and any(word in asked for word in DIRECTION_WORDS):
            return [UNGOVERNED_DIRECTION_NOTE]
        return []

    def run(self, request: SQLGenerationRequest) -> SQLGenerationResponse:
        grounding = request.grounding
        index = grounding_index(grounding)
        if not index.tables:
            return self._refusal(request, ["grounding packet contains no table"])

        attempts = 0
        accumulated: list[CheckViolation] = []

        plan: QueryPlan | None = None
        violations: list[CheckViolation] = []
        for _ in range(self.max_attempts):
            attempts += 1
            try:
                candidate = self.provider.generate("query_plan", self._plan_prompt(request, violations), QueryPlan)
            except (ValueError, ValidationError) as exc:
                violations = [CheckViolation(code="unparsable_plan", message=str(exc))]
                accumulated.extend(violations)
                continue
            violations = check_plan(candidate, grounding)
            accumulated.extend(violations)
            if not violations:
                plan = candidate
                break

        if plan is None:
            return SQLGenerationResponse(
                status="check_failed",
                semantic_version=grounding.semantic_version,
                dialect=request.dialect,
                violations=accumulated,
                attempts=attempts,
                provider=self.provider.name,
                model=self.provider.model,
            )

        sql = ""
        violations = []
        for _ in range(self.max_attempts):
            attempts += 1
            try:
                draft = self.provider.generate("sql_draft", self._sql_prompt(request, plan, violations), SQLDraft)
            except (ValueError, ValidationError) as exc:
                violations = [CheckViolation(code="unparsable_sql", message=str(exc))]
                accumulated.extend(violations)
                continue
            sql = draft.sql
            violations = check_sql(sql, plan, grounding, disclosure_cap=self.disclosure_cap)
            if not violations and self.connection is not None:
                violations = check_engine(sql, self.connection)
            accumulated.extend(violations)
            if violations:
                continue

            if self.execute is None:
                break
            try:
                result = self.execute(sql, request.max_rows)
            except Exception as exc:
                violations = [CheckViolation(code="execution_error", message=str(exc))]
                accumulated.extend(violations)
                continue
            return SQLGenerationResponse(
                status="ok",
                semantic_version=grounding.semantic_version,
                dialect=request.dialect,
                sql=sql,
                plan=plan,
                result=result,
                assumptions=self._assumptions(request, plan),
                used_grounding_ids=sorted(set(plan.tables) | set(plan.joins) | set(plan.metric_ids)),
                attempts=attempts,
                provider=self.provider.name,
                model=self.provider.model,
            )

        if not violations:
            return SQLGenerationResponse(
                status="ok",
                semantic_version=grounding.semantic_version,
                dialect=request.dialect,
                sql=sql,
                plan=plan,
                assumptions=self._assumptions(request, plan),
                used_grounding_ids=sorted(set(plan.tables) | set(plan.joins) | set(plan.metric_ids)),
                attempts=attempts,
                provider=self.provider.name,
                model=self.provider.model,
            )

        return SQLGenerationResponse(
            status="check_failed",
            semantic_version=grounding.semantic_version,
            dialect=request.dialect,
            sql=sql,
            plan=plan,
            violations=accumulated,
            attempts=attempts,
            provider=self.provider.name,
            model=self.provider.model,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_text2sql_agent.py -v`
Expected: 8 passed

- [ ] **Step 5: Add the FR-715 assumption test**

Append to `tests/test_text2sql_agent.py`:

```python
def test_directional_question_records_an_ungoverned_assumption():
    # Verified: this phrasing retrieves seven tables including table.transactions.
    grounding = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE)).grounding("total outflow by month")
    index = grounding_index(grounding)
    plan = QueryPlan(
        intent="outflow by month",
        grain="one row per month",
        tables=["table.transactions"],
        columns=["amount", "txn_type", "txn_date"],
        group_by=["month"],
        warnings_addressed=index.warnings_by_object["table.transactions"],
    )
    sql = "SELECT date_trunc('month', txn_date) AS month, SUM(amount) AS outflow FROM transactions GROUP BY 1"
    provider = ScriptedProvider([plan, SQLDraft(sql=sql)])
    response = Text2SQLAgent(provider, execute=lambda s, c: _result()).run(
        SQLGenerationRequest(question="What is total outflow by month?", grounding=grounding)
    )

    assert response.status == "ok"
    assert any("not governed" in note for note in response.assumptions)
```

The phrasing is not arbitrary. `"total outflow by month"` was checked against
the retriever and returns `table.accounts`, `table.branches`, `table.customers`,
`table.employees`, `table.loan_payments`, `table.loans`, and
`table.transactions`. Retrieval is deterministic for a fixed bundle, so this
holds as long as the bundle version does not change.

Also add the missing import to the test file's header:

```python
from cerebro.paths import DEFAULT_BUNDLE
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/cerebro/text2sql.py tests/test_text2sql_agent.py
git commit -m "feat: add two-stage grounded text-to-sql agent"
```

---

### Task 8: Read-only executor with row cap and timeout

Implements FR-718 to FR-722. Verifies T-711, T-712.

**Files:**
- Create: `src/cerebro/executor.py`
- Create: `tests/test_executor.py`

**Interfaces:**
- Produces:
  - `class DuckDBExecutor` constructed as `DuckDBExecutor(database_path, timeout_seconds=30)`
  - `DuckDBExecutor.__call__(sql: str, max_rows: int) -> QueryResult` so it satisfies `Executor`
  - `DuckDBExecutor.connection` for passing to `check_engine`
  - `DuckDBExecutor.close() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_executor.py`:

```python
from __future__ import annotations

import duckdb
import pytest

from cerebro.executor import DuckDBExecutor


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "t.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t (a BIGINT, b VARCHAR)")
    con.execute("INSERT INTO t SELECT i, 'x' FROM range(10) AS s(i)")
    con.close()
    return path


def test_returns_metadata_and_rows(database):
    executor = DuckDBExecutor(database)
    result = executor("SELECT a, b FROM t ORDER BY a", max_rows=100)
    executor.close()

    assert result.columns == ["a", "b"]
    assert result.column_types == ["BIGINT", "VARCHAR"]
    assert result.row_count == 10
    assert result.truncated is False
    assert result.elapsed_ms >= 0


def test_row_cap_truncates_rather_than_raising(database):
    executor = DuckDBExecutor(database)
    result = executor("SELECT a FROM t ORDER BY a", max_rows=3)
    executor.close()

    assert result.row_count == 3
    assert result.truncated is True


def test_zero_rows_is_not_an_error(database):
    executor = DuckDBExecutor(database)
    result = executor("SELECT a FROM t WHERE a > 999", max_rows=100)
    executor.close()

    assert result.row_count == 0
    assert result.truncated is False


def test_connection_is_read_only(database):
    executor = DuckDBExecutor(database)
    with pytest.raises(Exception):
        executor.connection.execute("CREATE TABLE blocked (x INTEGER)")
    executor.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_executor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebro.executor'`

- [ ] **Step 3: Write minimal implementation**

Create `src/cerebro/executor.py`:

```python
"""Read-only DuckDB execution for the demo path.

This is demo glue, not the Governed Query Executor described in the README. It
bounds rows and time. It does not do identity, authorization, audit logging, or
cost accounting.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

from .models import QueryResult


class DuckDBExecutor:
    def __init__(self, database_path: Path | str, timeout_seconds: int = 30):
        self.connection = duckdb.connect(str(database_path), read_only=True)
        self.connection.execute(f"SET statement_timeout = {int(timeout_seconds) * 1000}")

    def __call__(self, sql: str, max_rows: int) -> QueryResult:
        started = time.perf_counter()
        cursor = self.connection.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        description = cursor.description or []
        return QueryResult(
            columns=[column[0] for column in description],
            column_types=[str(column[1]) for column in description],
            rows=[list(row) for row in rows],
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    def close(self) -> None:
        self.connection.close()
```

If `SET statement_timeout` is rejected by the installed DuckDB build, replace
that line with `SET statement_timeout = '30s'` and adjust the argument
accordingly; check `SELECT * FROM duckdb_settings() WHERE name LIKE '%timeout%'`
to see the accepted form. Do not silently drop the timeout.

If `column_types` comes back as Python type objects rather than DuckDB type
names, use `cursor.types` instead of `description[1]`; assert against the real
values you observe rather than changing the test to match a bug.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_executor.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cerebro/executor.py tests/test_executor.py
git commit -m "feat: add read-only executor with row cap and timeout"
```

---

### Task 9: CLI command and baseline evaluation artifact

Implements AC-708, AC-709, AC-700 to AC-706 assertions. Verifies T-713, T-714, T-717.

**Files:**
- Modify: `src/cerebro/cli.py`
- Modify: `src/cerebro/evaluation.py`
- Create: `tests/test_baseline_evaluation.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 8
- Produces:
  - `cerebro.evaluation.build_agent(database_path, provider=None) -> tuple[Text2SQLAgent, DuckDBExecutor]` — returns the executor too, because the caller owns closing it
  - `cerebro.evaluation.run_sql_baseline(bundle_path, questions_path, database_path, provider=None, artifact_path=None) -> dict`
  - CLI subcommands `cerebro ask "<question>"` and `cerebro baseline`

- [ ] **Step 1: Write the failing test**

Create `tests/test_baseline_evaluation.py`:

```python
from __future__ import annotations

import json

import pytest

from cerebro.evaluation import run_sql_baseline
from cerebro.paths import ROOT

DATABASE = ROOT / "data" / "workshop.duckdb"
pytestmark = pytest.mark.skipif(not DATABASE.exists(), reason="run scripts/load_duckdb.py first")


def test_baseline_report_shape_and_gate(tmp_path):
    report = run_sql_baseline(database_path=DATABASE)

    assert set(report) >= {"questions", "totals", "model", "provider", "semantic_version"}
    assert len(report["questions"]) == 10
    for entry in report["questions"]:
        assert entry["status"] in {"ok", "check_failed", "refused"}
        assert "attempts" in entry
        assert "violations" in entry

    # AC-709: a contract violation may never reach execution.
    for entry in report["questions"]:
        if entry["violations"]:
            assert entry["status"] == "check_failed"
            assert entry["row_count"] is None


def test_relative_time_question_anchors_on_max_txn_date():
    report = run_sql_baseline(database_path=DATABASE)
    entry = next(item for item in report["questions"] if item["id"] == "GQ-08")
    if entry["status"] != "ok":
        pytest.skip(f"GQ-08 did not reach ok on the baseline model: {entry['status']}")
    sql = entry["sql"].upper()
    assert "MAX(" in sql
    assert "CURRENT_DATE" not in sql and "NOW()" not in sql


def test_branch_question_uses_both_declared_joins():
    report = run_sql_baseline(database_path=DATABASE)
    entry = next(item for item in report["questions"] if item["id"] == "GQ-05")
    if entry["status"] != "ok":
        pytest.skip(f"GQ-05 did not reach ok on the baseline model: {entry['status']}")
    assert "relationship.transaction_account" in entry["joins"]
    assert "relationship.account_branch" in entry["joins"]


def test_baseline_artifact_is_written(tmp_path):
    target = tmp_path / "baseline.json"
    run_sql_baseline(database_path=DATABASE, artifact_path=target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert len(payload["questions"]) == 10
```

The three per-question tests skip rather than fail when the baseline model does
not reach `ok`. That is deliberate: AC-703 and AC-705 constrain the *shape* of a
successful answer, while the pass rate itself is the number spec 010 improves.
Failing the build on a 3B model's pass rate would make the baseline unmeasurable.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_baseline_evaluation.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_sql_baseline'`

- [ ] **Step 3: Write minimal implementation**

Extend `src/cerebro/evaluation.py`. Move the six import lines below up into the
existing import block at the top of the file rather than leaving them mid-file;
`yaml`, `Path`, `DEFAULT_BUNDLE`, `ROOT`, `load_validated_bundle`, and
`SemanticRetriever` are already imported there and are reused as-is.

```python
import json
from datetime import datetime, timezone

from .executor import DuckDBExecutor
from .hosted_provider import provider_from_environment
from .models import SQLGenerationRequest
from .text2sql import Text2SQLAgent


def build_agent(database_path: Path | str, provider=None) -> tuple[Text2SQLAgent, DuckDBExecutor]:
    resolved = provider or provider_from_environment()
    if resolved is None:
        raise RuntimeError(
            "No provider configured; set CEREBRO_API_KEY and CEREBRO_MODEL, "
            "or pass a provider explicitly"
        )
    executor = DuckDBExecutor(database_path)
    agent = Text2SQLAgent(resolved, execute=executor, connection=executor.connection)
    return agent, executor


def run_sql_baseline(
    bundle_path: Path | str = DEFAULT_BUNDLE,
    questions_path: Path | str = ROOT / "evaluation" / "golden-questions.yaml",
    database_path: Path | str = ROOT / "data" / "workshop.duckdb",
    provider=None,
    artifact_path: Path | str | None = None,
) -> dict:
    retriever = SemanticRetriever(load_validated_bundle(bundle_path))
    cases = yaml.safe_load(Path(questions_path).read_text(encoding="utf-8"))["questions"]
    agent, executor = build_agent(database_path, provider)
    entries = []
    try:
        for case in cases:
            grounding = retriever.grounding(case["question"], limit=10)
            response = agent.run(SQLGenerationRequest(question=case["question"], grounding=grounding))
            entries.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "status": response.status,
                    "attempts": response.attempts,
                    "sql": response.sql,
                    "joins": list(response.plan.joins) if response.plan else [],
                    "row_count": response.result.row_count if response.result else None,
                    "violations": [v.code for v in response.violations],
                    "assumptions": response.assumptions,
                    "used_grounding_ids": response.used_grounding_ids,
                }
            )
    finally:
        executor.close()

    totals: dict[str, int] = {}
    for entry in entries:
        totals[entry["status"]] = totals.get(entry["status"], 0) + 1
    decoding_defects = sum(
        1 for entry in entries if {"unparsable_plan", "unparsable_sql"} & set(entry["violations"])
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "semantic_version": retriever.bundle.version,
        "provider": agent.provider.name,
        "model": agent.provider.model,
        "adapter": None,
        "questions": entries,
        "totals": totals,
        "decoding_defects": decoding_defects,
    }
    if artifact_path is not None:
        target = Path(artifact_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_baseline_evaluation.py -v`
Expected: 4 passed or skipped, none failed

- [ ] **Step 5: Wire the CLI**

In `src/cerebro/cli.py`, add these subparsers inside `build_parser` after the `evaluate` block:

```python
    ask = commands.add_parser("ask", help="Answer one question through the grounded agent")
    ask.add_argument("question")
    ask.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    ask.add_argument("--database", type=Path, default=ROOT / "data" / "workshop.duckdb")
    baseline = commands.add_parser("baseline", help="Run the golden set and write the baseline artifact")
    baseline.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    baseline.add_argument("--database", type=Path, default=ROOT / "data" / "workshop.duckdb")
    baseline.add_argument("--output", type=Path, default=ROOT / "artifacts" / "baseline.json")
```

Add these branches inside `main` before the `serve` branch:

```python
        elif args.command == "ask":
            from .evaluation import build_agent
            from .models import SQLGenerationRequest
            from .retrieval import SemanticRetriever

            retriever = SemanticRetriever(load_validated_bundle(args.bundle))
            agent, executor = build_agent(args.database)
            try:
                response = agent.run(
                    SQLGenerationRequest(
                        question=args.question,
                        grounding=retriever.grounding(args.question, limit=10),
                    )
                )
            finally:
                executor.close()
            print(response.model_dump_json(indent=2))
            return 0 if response.status == "ok" else 1
        elif args.command == "baseline":
            from .evaluation import run_sql_baseline

            report = run_sql_baseline(
                bundle_path=args.bundle, database_path=args.database, artifact_path=args.output
            )
            for entry in report["questions"]:
                print(f"{entry['status']:<13} {entry['id']}  attempts={entry['attempts']}  {entry['question']}")
            print(f"\ntotals: {report['totals']}  decoding_defects: {report['decoding_defects']}")
            print(f"artifact: {args.output}")
            return 0
```

Update the imports at the top of `cli.py`:

```python
from .bundle import BundleLoader, BundleValidator, load_validated_bundle
from .paths import DEFAULT_BUNDLE, DEFAULT_CONFIG, ROOT
```

- [ ] **Step 6: Run the agent end to end**

```bash
python -m cerebro ask "What is the fraud rate by card type?"
python -m cerebro baseline
```

Expected: the first prints a JSON response; the second prints ten status lines
and writes `artifacts/baseline.json`. Record the totals line — that is the
AC-708 baseline that spec 010 is measured against, and spec 010's AC-904 blocks
training until this file exists.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass or skip; none fail

- [ ] **Step 8: Commit**

```bash
git add src/cerebro/cli.py src/cerebro/evaluation.py tests/test_baseline_evaluation.py
git commit -m "feat: add ask command and baseline evaluation artifact"
```

---

## Spec Coverage

| Spec requirement | Task |
|---|---|
| FR-700, FR-701 | 1 |
| FR-702, FR-703, FR-703a, FR-703b, FR-703c | 6 |
| FR-704 to FR-708 | 2, 7 |
| FR-709, FR-710 | 3 |
| FR-711, FR-712, FR-713 | 4 |
| FR-714 | 5 |
| FR-715, FR-716, FR-717 | 7 |
| FR-718 to FR-722 | 8 |
| AC-700 to AC-706 | 3, 4, 7, 9 |
| AC-707 | 6 |
| AC-708, AC-709 | 9 |

## Known Deviations From The Spec

Both are deliberate and should be reviewed rather than silently accepted.

**FR-715 uses a keyword heuristic.** `Text2SQLAgent._assumptions` decides a
question is directional by matching words against `DIRECTION_WORDS`. The spec
says an inferred mapping must be recorded; it does not say how the agent knows
inference happened. A keyword list will miss phrasings. The alternative is to
have the model declare the inference in the plan, which means trusting the model
to self-report and gives a weaker guarantee. The heuristic is the safer of two
imperfect options for a demo, and the right fix is upstream: a declaration in
the bundle removes the need entirely.

**`check_engine` runs only when a connection is supplied.** `Text2SQLAgent`
accepts `connection=None`, in which case FR-714 does not run. This keeps the
agent unit-testable without a database, and Task 9 always supplies a real
connection on the live path. A reviewer should confirm no production entry point
constructs the agent without one.
