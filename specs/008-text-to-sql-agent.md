# 008 — Text-to-SQL Agent

## Problem

Spec 005 produces a grounding packet but nothing consumes it. A business question still cannot become an executed query.

An ungrounded model writing SQL against this schema produces answers that look right and are wrong. Three failures are verifiable in the shipped data:

- `transactions.amount` is unsigned across 2,000,000 rows; direction exists only in `txn_type`. `SUM(amount)` for an outflow question silently adds inflows.
- Transaction dates stop at `2026-06-30`. Any relative window built on `CURRENT_DATE` yields empty trailing periods and a wrong growth figure.
- `table.transactions` and `table.branches` share no key. The only legal route is through `table.accounts`, two declared joins.

Each failure is already described in the bundle as a warning. The agent's job is to be provably bound by those warnings rather than to be reminded of them.

## Goal

A bounded, locally hosted agent that turns a natural-language question plus a grounding packet into a query plan, then dialect-correct DuckDB SQL, verifies both against the semantic contract deterministically, and executes read-only aggregate queries.

## Non-Goals

- Orchestration, conversation memory, multi-turn clarification.
- Governed execution concerns: identity, authorization, audit logging, cost accounting, tenant isolation. The execution path here is demo glue, not the Governed Query Executor described in the README.
- Self-hosting a model. Generation runs against a hosted provider using an organizer-supplied key.
- Synthetic dataset generation and any weight adaptation (spec 010).
- Narrative interpretation of results (spec 009).
- Editing the OKF bundle. It is read-only input owned by another workstream.

## Runtime Foundation

- FR-700: Materialize `archive/*.csv` into DuckDB using DDL derived from the bundle's `cerebro.columns` declarations, not CSV type inference. Column names, order, and declared types (`BIGINT`, `VARCHAR`, `DOUBLE`, `DATE`) come from the ten table documents.
- FR-701: Load fails closed on any divergence between a CSV header and the bundle's declared column list for that table.
- FR-702: Generation uses a hosted provider reached with an organizer-supplied API key, through an adapter implementing the existing `GenerationProvider` interface from `enrichment.py`. Credentials are read from the environment, never committed, and never written to logs, artifacts, or error messages.
- FR-703: Both structured stages request provider-native structured output bound to the target JSON schema, using whichever mechanism the provider offers. When output still fails Pydantic validation, the attempt is counted in `attempts`, the violation `unparsable_plan` or `unparsable_sql` is recorded, and the run proceeds through the normal retry budget. The count is reported by the evaluation harness so structural reliability is measured rather than assumed.
- FR-703a: Prompt egress is bounded. Prompts may carry the question, grounding packet, and prior violations. Prompts may not carry source rows. This preserves the boundary spec 003 established for enrichment, now that inference is remote.
- FR-703b: Transport faults are distinguished from model faults. Rate limiting, timeout, and server error are retried with bounded exponential backoff and do not consume the FR-716 semantic retry budget. Exhausted transport retries surface as `status = check_failed` with violation `provider_unavailable`.
- FR-703c: The test suite runs without the API key. Provider interactions are recorded once to fixtures and replayed offline, so no test requires network access or credentials. This preserves the property the repo already had when generation was optional.

## Functional Requirements

### Stages and contract

- FR-704: Stage 1 maps question plus grounding to a `QueryPlan`. Stage 2 maps question plus plan plus grounding to SQL. Each stage emits schema-validated structured output.
- FR-705: The public contract is `SQLGenerationRequest` and `SQLGenerationResponse` with `status` in `ok | check_failed | refused`.
- FR-706: `status = ok` means every check in FR-709 through FR-715 passed and execution succeeded. Only this status carries a result.
- FR-707: `status = refused` when the grounding packet lacks an object the question requires. `unmet_needs` names what was missing. No SQL is emitted and no execution is attempted.
- FR-708: The response records `semantic_version`, `dialect`, `used_grounding_ids`, `assumptions`, `attempts`, `provider`, and `model`.

### Deterministic self-check

No model participates in any check below. All checks run in `selfcheck.py` and are unit-testable without a provider.

- FR-709: Plan containment. `plan.tables`, `plan.columns`, `plan.metric_ids`, and `plan.joins` must each be subsets of the corresponding grounding collections. Joins are referenced by `relationship.*` id, never by hand-written predicates.
- FR-710: Warning coverage. Every warning attached to any grounding object named in the plan must appear in `plan.warnings_addressed`. Missing coverage is violation `unaddressed_warning`.
- FR-711: Metric fidelity. For each `plan.metric_ids` entry, the governed `formula` string from grounding must appear in the SQL projection. Comparison is by `sqlglot`-normalized AST subtree match, never string equality, because the model may alias tables.
- FR-712: Statement shape. Reject anything that is not a single `SELECT`. Multiple statements, DDL, DML, and `PRAGMA` are violations before any engine contact.
- FR-713: Classification guard, bounded disclosure. `policy.sensitive-banking-data` requires aggregate results with restricted fields minimized. "Minimized" is implemented as a bound on disclosure volume, not as a prohibition:
  - A `restricted` or `confidential` column may appear in the output projection only when the statement carries an explicit row limit whose value is at or below the disclosure cap. Default cap is 50 rows; it is configuration, per classification tier.
  - A projection containing such a column with no row limit, or with a limit above the cap, is violation `unbounded_sensitive_projection`. This is the case that matters: returning five customer names answers a question, returning sixty thousand is a data extract.
  - Every disclosure is recorded. The agent appends an `assumptions` entry naming the disclosed columns, the row bound, and `policy.sensitive-banking-data` as the governing object. Spec 009 turns that entry into a reader-visible caveat.
  - Aggregates over these columns remain unrestricted, since no individual value is exposed.

  Authorization is deliberately not modelled here. Deciding *who* may see a name belongs to the Governed Query Executor, which is out of scope. This requirement bounds volume and guarantees the disclosure is never silent.
- FR-714: Engine validation. Run `EXPLAIN` on a read-only connection before execution. Unknown identifiers, type errors, and binder errors become violations without returning data.
- FR-715: Ungoverned inference. The value-to-direction mapping for `txn_type` is not declared anywhere in the bundle. The agent must not present an inferred mapping as governed; the inference is recorded in `assumptions` naming the values it treated as inflow and outflow.
- FR-716: Bounded retry. Stage 1 gets at most two attempts, gated by FR-709 and FR-710. Stage 2 gets at most two attempts, gated by FR-711 through FR-714 and by execution failure under FR-721. The two budgets are independent and are not shared. Each retry receives the accumulated violations as revision input. Exhausting either budget yields `check_failed` carrying every violation observed across all attempts.
- FR-717: `check_failed` returns the last SQL for human inspection and marks it non-executable. Callers must not execute it.

### Execution

- FR-718: Execute only after all checks pass, on a read-only connection.
- FR-719: Enforce a row cap and a statement timeout. Defaults are 1000 rows and 30 seconds, both configuration rather than literals. Exceeding the row cap sets `truncated` rather than raising. Exceeding the timeout is violation `execution_error`.
- FR-720: Return column names, column types, rows, `row_count`, `truncated`, and `elapsed_ms`.
- FR-721: An execution error retries within the FR-716 budget and then becomes violation `execution_error` with `status = check_failed`.
- FR-722: A zero-row result is a successful outcome, not a failure, and never triggers a retry. Zero fraud cases in a period is a correct answer.

## Acceptance Criteria

- AC-700: No executed SQL references a table, column, join, or metric absent from the grounding packet supplied for that question.
- AC-701: No executed SQL projects a `restricted` or `confidential` column without a row limit at or below the disclosure cap. A question asking for a small named set, such as the top five customers by income, succeeds and carries a disclosure entry in `assumptions`. A question asking for all customer names is refused as `unbounded_sensitive_projection`.
- AC-702: Every answer using a metric contains that metric's governed formula, verified by AST comparison.
- AC-703: A question requiring a relative time window anchors to `MAX(txn_date)`. A generated query containing `CURRENT_DATE` or `NOW()` for such a question fails the acceptance run.
- AC-704: A question outside the bundle's coverage, for example ATM counts, returns `refused` with populated `unmet_needs`. It never returns fabricated SQL.
- AC-705: A branch-level transaction question routes through `relationship.transaction_account` and `relationship.account_branch`. Any direct predicate between `transactions` and `branches` fails the run.
- AC-706: Determinism. Identical question, identical grounding, temperature zero yields an identical plan and identical SQL across runs.
- AC-707: The full test suite passes with no API key present and networking disabled, using replayed provider fixtures. Credentials never appear in logs, artifacts, committed files, or error messages. No prompt sent to the provider contains a source row.
- AC-708: The evaluation harness records per-question status for all ten golden questions using the un-fine-tuned model. This number is the baseline required by spec 010 and must exist before any fine-tuning begins.
- AC-709: No question produces an executed query that violates the semantic contract. This criterion is structural rather than statistical: FR-709 through FR-714 gate execution, so a contract violation can only terminate in `check_failed`, never in an executed query. `check_failed` is an acceptable terminal state and is counted, not treated as a build failure.

## Edge Cases

- Empty or near-empty grounding packet.
- Metric requested with no dimension, and metric requested with a dimension whose join is two hops away.
- Question spanning `transactions` and `card_transactions`, where a naive `UNION` of raw rows double-counts; `concept.active-customer` warns against it explicitly.
- Legitimate zero-row result versus a zero-row result caused by a wrong filter.
- Grouping key with high cardinality, for example `customer_id` across 60,000 customers.
- DuckDB date functions applied to a column the bundle declares `DATE` but a bad load typed `VARCHAR`.
- Question phrased in Vietnamese while the model and bundle are English.
- Model emits a plan that is schema-valid but semantically empty, for example zero tables.

## Interfaces / Contracts

```python
class QueryPlan(BaseModel):
    intent: str
    grain: str
    tables: list[str]
    columns: list[str]
    metric_ids: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)          # relationship.* ids only
    filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    row_limit: int | None = None
    warnings_addressed: list[str] = Field(default_factory=list)


class CheckViolation(BaseModel):
    code: Literal[
        "unknown_table", "unknown_column", "undeclared_join", "unknown_metric",
        "formula_not_verbatim", "unaddressed_warning",
        "unbounded_sensitive_projection", "non_select_statement",
        "explain_failed", "execution_error", "unparsable_sql", "unparsable_plan",
    ]
    message: str
    subject: str = ""


class QueryResult(BaseModel):
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class SQLGenerationRequest(BaseModel):
    question: str
    grounding: GroundingResponse          # reused unchanged from spec 005
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

Module boundaries:

| Module | Responsibility | Needs a model? |
|---|---|---|
| `src/cerebro/text2sql.py` | two stages, retry loop, execution | yes |
| `src/cerebro/selfcheck.py` | FR-709 to FR-714, pure functions | no |
| `src/cerebro/hosted_provider.py` | `GenerationProvider` over the organizer API, plus fixture record and replay | n/a |
| `scripts/load_duckdb.py` | FR-700, FR-701, build-time only | no |

`selfcheck.py` is separate from `text2sql.py` so the entire check suite is testable with fixture JSON and no model running.

## Constraints

- Hosted inference through an organizer-supplied API key. The key is a shared, quota-bearing resource: every stage call, retry, and evaluation run consumes it. Evaluation over the golden set must be runnable from replayed fixtures so that iterating on the checker does not burn quota.
- Temperature is zero for both stages. Reasoning or thinking modes are disabled where the provider exposes the choice, since they degrade adherence to a fixed output schema.
- `sqlglot` is the only SQL parser. No regular-expression SQL analysis.
- DuckDB connections are opened read-only.
- The bundle at `knowledge/bank-workshop/` is not modified by this spec.

## Assumptions

- `archive/*.csv` is the authoritative source data. Verified against the bundle: ten tables, seventy-five columns, matching names in matching order.
- Grounding is supplied by the caller as a value. The agent performs no retrieval and holds no session state.
- `config/bank-source.yaml` is updated to a local `database_path`; the committed `/home/kwan/...` path is stale.
- Demo language is English.
- The organizer-supplied key grants access to a hosted instruction-following model capable of schema-bound structured output. Provider identity, model identity, quota, and retention terms are recorded in `config/` once known, and the adapter treats them as configuration rather than assumptions baked into code.
- No commitment is made about provider-side data retention. FR-703a therefore restricts prompt content by construction rather than relying on a provider guarantee.
- Module layout stays flat under `src/cerebro/`, matching the nine existing modules. No subpackages are introduced.
- Classification is read from grounding at runtime and never hard-coded. For test design only, the current bundle inventory is five `restricted` columns (`customers.name`, `customers.date_of_birth`, `customers.phone`, `customers.email`, `employees.name`) and four `confidential` columns (`accounts.balance`, `customers.annual_income`, `customers.credit_score`, `employees.salary`).

## Open Questions

- Whether the semantic-layer owner will declare the `txn_type` direction mapping in the bundle. Until then, FR-715 applies and directional answers carry an assumption.
- Which provider and model the organizer key targets, and what quota it carries. The adapter is written provider-neutral so this answer changes configuration, not code. Quota size determines how often the golden set can be run live rather than from fixtures.
- Whether the key permits weight adaptation. Spec 010 depends on this answer; if it does not, that spec pivots from fine-tuning to few-shot exemplar retrieval over the same generated corpus.
- Whether `needs_clarification` becomes a fourth status. Deferred; adding it is a contract version bump.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-700 | FR-700, FR-701 | Load ten CSVs; assert row counts and that `information_schema` types match the bundle. Assert a mutated CSV header fails the load. |
| T-701 | FR-702, FR-703, AC-707 | Provider returns schema-valid `QueryPlan` JSON for a fixture question. Repeated with networking disabled at the OS level to prove no cloud dependency. Decoding defect count surfaced in `attempts`. |
| T-702 | FR-704 to FR-708 | Contract round-trip for each status; assert `refused` carries `unmet_needs` and emits no SQL. |
| T-703 | FR-709, AC-700 | Fixture plans naming an absent table, column, join, and metric each raise the matching violation code, and no such plan reaches execution. |
| T-704 | FR-710 | A plan using `table.transactions` without its three warnings raises `unaddressed_warning`. |
| T-705 | FR-711, AC-702 | Aliased SQL (`card_transactions AS ct`) still passes AST formula match; an altered formula fails. Every golden question naming a metric carries that metric's formula. |
| T-706 | FR-712 | Multi-statement, non-`SELECT`, DDL, DML, and `PRAGMA` inputs rejected before engine contact. |
| T-707 | FR-713, AC-701 | Table-driven disclosure test: `customers.name` with `LIMIT 5` accepted and produces an `assumptions` entry naming the policy; same projection with no limit and with `LIMIT 1000` both raise `unbounded_sensitive_projection`; `AVG(annual_income)` accepted with no limit. |
| T-708 | FR-715 | A directional question over `transactions` yields an `assumptions` entry naming the `txn_type` values treated as inflow and outflow. Absent that entry the test fails, so an ungoverned inference cannot pass silently. |
| T-709 | FR-714 | Unknown column produces `explain_failed` and returns no rows. |
| T-710 | FR-716, FR-717, FR-721 | Retry budget respected per stage and not shared; `check_failed` accumulates violations across attempts and marks SQL non-executable. |
| T-711 | FR-718 to FR-720 | Row cap sets `truncated`; timeout enforced; result metadata complete. |
| T-712 | FR-722 | A filter guaranteeing zero rows returns `ok` with `row_count = 0` and no retry. |
| T-713 | AC-703 | Golden question GQ-08 assertion: SQL contains `MAX(` over `txn_date` and contains neither `CURRENT_DATE` nor `NOW()`. |
| T-714 | AC-705 | Golden question GQ-05 assertion: both required relationship ids present in `plan.joins`. |
| T-715 | AC-704 | Out-of-scope question fixture returns `refused`. |
| T-716 | AC-706 | Two consecutive runs produce byte-identical plan and SQL. |
| T-717 | AC-708, AC-709 | Evaluation harness over all ten golden questions records status per question, writes the baseline artifact, and asserts no contract violation reached execution. |
| T-718 | FR-703a | Serialize the prompt for every golden question and assert none contains a value drawn from any of the ten source tables. The assertion runs against the outbound payload, not against the prompt template. |
| T-719 | FR-703b | Stubbed provider returns rate-limit, timeout, and server-error responses in turn; assert bounded backoff, assert the FR-716 semantic budget is untouched, and assert exhaustion yields `provider_unavailable`. |
| T-720 | FR-703c | The whole suite runs green with the key environment variable unset and networking disabled, driven by recorded fixtures. A missing fixture fails loudly rather than falling through to a live call. |
