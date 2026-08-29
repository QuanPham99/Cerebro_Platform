# 009 — Insight and Report Agent

## Problem

A result set is not an answer. Spec 008 returns rows; nobody has said what they mean, under which definition, at which grain, or with which caveats.

Three constraints make naive narration unsafe here:

- Scale. `card_transactions` holds 3,000,000 rows and `transactions` 2,000,000. Raw rows can never be handed to a model, so interpretation must work from aggregates only.
- Governance. `policy.sensitive-banking-data` applies to all ten tables and requires aggregate output with restricted fields minimized. A report is an output surface and inherits that rule.
- Hallucinated numbers. A report that states a figure absent from the query result is worse than no report, because it carries the authority of the pipeline that produced it.

The bundle already holds what a trustworthy report needs. `metric.card-fraud-rate` carries its formula and the note that safe division returns `NULL` on an empty population. `concept.support-workload` warns that satisfaction score is meaningful only for resolved cases. These are the caveats a competent analyst would add, and they are machine-readable.

## Goal

Turn a question, its `QueryPlan`, its grounding packet, and an aggregate result into a structured analysis whose every number is traceable to a result cell or a deterministically derived value, and whose every caveat traces to a semantic warning.

## Non-Goals

- Chart rendering. This spec emits a chart specification; the UI draws it.
- Dashboard assembly or publishing.
- Re-querying, drill-down, or any second round trip to the database. The agent has no database access.
- Long-form prose. Output is bounded and structured.
- Recommending business actions.
- Cloud model APIs at runtime.

## Functional Requirements

### Input discipline

- FR-800: The contract is `InsightRequest` and `InsightReport` with `status` in `ok | insufficient_input`. Input carries question, `QueryPlan`, `GroundingResponse`, and `QueryResult`. The agent opens no database connection.
- FR-801: Aggregate-only. Return `insufficient_input` when `result.row_count` exceeds the interpretation cap, when `result.truncated` is set, or when the plan declares neither a `group_by` nor a metric. Row-level dumps are never interpreted.
- FR-802: Sensitive disclosure is reported, not blocked. Mapping is by `plan.columns`, which names grounding columns rather than SQL aliases. When a `plan.columns` entry resolves to a grounding column classified `restricted` or `confidential`, the report must carry a caveat whose `source_id` is `policy.sensitive-banking-data`, stating which columns were disclosed and under what row bound. Spec 008 FR-713 already bounds the volume; this requirement guarantees the reader is told. A missing disclosure caveat when such a column is present is a verifier failure, not a style issue.
- FR-802a: Individual sensitive values are never used as narrative subjects. A `restricted` column value may appear in the rendered result table and in a `Fact` of kind `cell`, but may not be referenced by a `Statement`. Findings describe the measure and the ranking, not the person.
- FR-802b: Sensitive values never leave the machine. Because inference is hosted, the prompt carries the fact `id`, `kind`, `label`, and `unit` for a sensitive cell but withholds its `value`. Substitution happens locally at render time under FR-807. The model can therefore compose a report about a named set without ever receiving a name.
- FR-802c: The same withholding applies to any result cell drawn from a `confidential` column when it is not already an aggregate. Aggregates are safe to send, since no individual value is recoverable from them.

### Deterministic fact extraction

Fact extraction runs before the model is prompted and involves no model.

- FR-803: Build a fact table from the result. For each numeric column emit `min`, `max`, `mean`, `sum`, and `count`. For each row emit its cell values.
- FR-804: Derive comparative facts deterministically: share of column total per row, rank per measure, spread as maximum minus minimum, ratio of highest to lowest, and top and bottom entries.
- FR-804a: The fact library is the only lever for richer phrasing. A comparison the model cannot express is resolved by adding a `kind`, never by relaxing FR-807 or FR-808.
- FR-805: When the plan names a time column, order rows by it and derive period-over-period delta and percent change.
- FR-806: Each fact receives a stable id, a human label, a value, a unit, and a source description naming the result cell or the derivation that produced it.

### Grounded narration

- FR-807: The model may not write numerals. It emits statements containing placeholders and a placeholder-to-fact-id mapping. Rendering substitutes formatted fact values.
- FR-808: Verification rejects any statement containing a digit outside a placeholder, and any placeholder that does not resolve to a fact id from FR-803 through FR-805. Dates and periods count as numeric and must also be fact references, carrying unit `date`. Number words spelled out, such as "three branches", are permitted because they carry no digit and therefore cannot misstate a value. This makes a wrong figure structurally impossible rather than unlikely.
- FR-809: Statement count and length are bounded. Findings are ordered by the magnitude of the fact they reference, not by model preference.

### Governed content

- FR-810: For every metric id in the plan, the report includes the metric's id, name, governed `formula` verbatim from grounding, `grain`, and `classification`. This block is copied, not generated.
- FR-811: Every caveat carries a `source_id`. The source must be a grounding object named in the plan that owns that warning. The model may reword the warning text for readability; the verifier checks the `source_id` linkage, not the wording.
- FR-812: Caveats additionally include, when applicable, a closed set of system notes: zero rows returned, result truncated, sensitive columns disclosed under a row bound, and any `assumptions` carried forward from the spec 008 response, including ungoverned inferences recorded under spec 008 FR-715.
- FR-813: The report reproduces `semantic_version` and `used_grounding_ids` from the spec 008 response so a reader can trace the answer back to bundle objects.

### Chart specification

- FR-814: The chart type is selected by deterministic rule from plan shape, not by the model. The vocabulary is closed: `single_value`, `line`, `bar`, `bar_top_n`, `grouped_bar`, `table`.
- FR-815: Selection rule. No dimension and one measure yields `single_value`. A time dimension yields `line`. One categorical dimension with cardinality at or below the display cap yields `bar`, above it `bar_top_n`. Two dimensions yield `grouped_bar`. Anything else yields `table`.
- FR-816: The chart specification names encoding channels by result column, and carries axis labels and the measure unit. It contains no styling.

### Outcomes

- FR-817: A zero-row result is a first-class success. The report explains that the population was empty and, when a metric with safe division is involved, states that the metric is undefined rather than zero.
- FR-818: Structured output uses the provider-native schema binding described in spec 008 FR-703. Schema-invalid output is recorded as a defect metric.
- FR-819: Generation shares the spec 008 provider adapter, key handling, transport retry policy, and fixture replay mechanism. This spec introduces no second provider and no second credential.

## Acceptance Criteria

- AC-800: No rendered report contains a number that is not a fact value from FR-803 through FR-805. Enforced structurally by FR-807 and FR-808, and asserted by an adversarial test that instructs the model to state a specific invented figure.
- AC-801: When the plan names an object carrying warnings, the report contains at least one caveat whose `source_id` is that object.
- AC-802: When the plan names a metric, the report contains that metric's governed formula, character for character.
- AC-803: A zero-row result yields `status = ok` with an explanatory report, never an error and never a claim of zero.
- AC-804: Chart type is a pure function of plan shape. Same plan shape yields same chart type across runs and across models.
- AC-805: A request whose result exceeds the interpretation cap or is truncated yields `insufficient_input` with a stated reason. A request carrying a bounded sensitive disclosure yields `ok` with a `policy.sensitive-banking-data` caveat, and no `Statement` references an individual sensitive value.
- AC-806: Reproducible at temperature zero for identical input.
- AC-807: The full test suite passes with no API key and networking disabled, using replayed provider fixtures. No prompt sent to the provider contains a `restricted` value, or a non-aggregated `confidential` value, or a source row.
- AC-808: Report generation never issues a database query. Asserted by running the agent with no DuckDB file present.

## Edge Cases

- Single-row, single-column result, where rank, share, and delta are all undefined.
- All-`NULL` measure column, which `NULLIF` safe division produces on an empty population.
- Metric returning `NULL` while row count is non-zero.
- Time series with gaps, where period-over-period delta must not silently treat adjacent rows as adjacent periods.
- Ties in ranking.
- Dimension cardinality just above and just below the display cap.
- A plan carrying an ungoverned assumption from spec 008 FR-715, which must surface as a caveat rather than disappear.
- Result column names that collide with fact ids.

## Interfaces / Contracts

```python
class Fact(BaseModel):
    id: str                                                  # f1, f2, ...
    kind: Literal["cell", "aggregate", "share", "rank",
                  "spread", "ratio", "delta", "pct_change"]
    label: str
    value: float | int | str | None
    unit: Literal["count", "currency", "percent", "ratio", "date", "text"]
    source: str                                              # result cell or derivation


class Statement(BaseModel):
    text: str                                                # placeholders only, e.g. "{f3}"
    refs: dict[str, str]                                     # placeholder -> fact id


class Caveat(BaseModel):
    source_id: str                                           # grounding object id or system note
    text: str


class MetricDefinition(BaseModel):
    id: str
    name: str
    formula: str                                             # verbatim from grounding
    grain: str
    classification: str


class ChartSpec(BaseModel):
    kind: Literal["single_value", "line", "bar", "bar_top_n", "grouped_bar", "table"]
    x: str | None = None
    y: str | None = None
    series: str | None = None
    x_label: str = ""
    y_label: str = ""
    unit: str = "count"


class InsightRequest(BaseModel):
    question: str
    plan: QueryPlan
    grounding: GroundingResponse
    result: QueryResult
    carried_assumptions: list[str] = Field(default_factory=list)


class InsightReport(BaseModel):
    status: Literal["ok", "insufficient_input"]
    semantic_version: str
    headline: Statement | None = None
    findings: list[Statement] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    caveats: list[Caveat] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    chart: ChartSpec | None = None
    used_grounding_ids: list[str] = Field(default_factory=list)
    reason: str = ""                                         # populated for insufficient_input
    provider: str = ""
    model: str = ""
```

Module boundaries:

| Module | Responsibility | Needs a model? |
|---|---|---|
| `src/cerebro/insight.py` | prompt, statement assembly, status | yes |
| `src/cerebro/facts.py` | FR-803 to FR-806, pure functions | no |
| `src/cerebro/report_check.py` | FR-808, FR-811, FR-814, FR-815 | no |

Three modules rather than one because the fact table and the verifier are the parts that must be provably correct, and both are testable with fixture results and no model running.

## Constraints

- Shares the spec 008 hosted provider, model, and key. No second provider and no second credential are introduced.
- Every report costs quota. The fact table, the verifier, and the chart rule are deterministic and cost nothing, so iterating on them must not require live calls.
- Output language is English.
- No database access from this agent, by construction.
- Interpretation cap defaults to 200 rows and display cap to 25 categories. Both are configuration, not hard-coded literals.
- Findings are bounded at five statements, each at most 200 characters after substitution.

## Assumptions

- Input arrives from a spec 008 response with `status = ok`. Handling `check_failed` or `refused` belongs to the caller.
- Demo language is English, so a single coder-instruct model can serve both agents. Vietnamese narration at this parameter count was assessed as unreliable and is out of scope.
- Warnings in the bundle are analyst-grade and can be surfaced to a reader with light rewording.

## Open Questions

- Interpretive accuracy is not covered by any verifier here. FR-807 and FR-808 guarantee that every figure is correct; they do not prevent a statement that draws a wrong conclusion from correct figures, for example asserting concentration where the spread is negligible. FR-809 constrains emphasis by ordering findings on fact magnitude, which reduces but does not remove the risk. Detection currently requires human reading of sample output. Whether a checkable rule exists for this class of error is open.
- Whether findings ordered by fact magnitude reads naturally, or whether a small ordering hint from the model is worth the loss of determinism. Resolved by looking at real output in week 3, not now.
- Whether the placeholder discipline in FR-807 produces prose acceptable for a live demo. Fallback if not: keep the discipline and lengthen the template library rather than allowing free numerals.
- Whether trend narration needs statistical significance treatment. Deferred.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-800 | FR-800, FR-802, FR-802a | Contract round-trip. A result carrying a `restricted` column yields `ok` plus a `policy.sensitive-banking-data` caveat; omitting that caveat fails the verifier. A `Statement` referencing a `restricted` cell fact is rejected. |
| T-801 | FR-801, AC-805 | Results above the cap, truncated results, and plans lacking both `group_by` and metric each yield `insufficient_input` with a distinct reason. A bounded sensitive disclosure yields `ok`, not `insufficient_input`. |
| T-802 | FR-803, FR-804, FR-806 | Fixture result produces expected `min`, `max`, `mean`, `sum`, `count`, share, rank, ratio, and spread facts. Every fact carries a unique id, label, value, unit, and a source naming the originating cell or derivation. |
| T-803 | FR-805 | Ordered time series produces correct delta and percent change. A series with a missing period does not treat adjacent rows as adjacent periods. |
| T-804 | FR-807, FR-808, AC-800 | Adversarial test: model is instructed to assert an invented figure. Verifier rejects the numeric literal and the unresolved placeholder. |
| T-805 | FR-809 | Findings are ordered by referenced fact magnitude; statement count and length within bounds. |
| T-806 | FR-810, AC-802 | Report for a fraud-rate question contains `100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0)` unmodified. |
| T-807 | FR-811, AC-801 | A plan naming `table.transactions` yields caveats whose `source_id` is `table.transactions`. A caveat with an unlinkable `source_id` is rejected. |
| T-808 | FR-812 | Carried assumptions from spec 008 FR-715 appear as caveats. Truncation and confidential-aggregation notes appear when applicable. |
| T-809 | FR-814, FR-815, FR-816, AC-804 | Table-driven test over plan shapes asserts the chart type for each, including cardinality at, below, and above the display cap. Encoding channels name result columns; the spec carries labels and unit and no styling keys. |
| T-810 | FR-817, AC-803 | Zero-row fixture yields `ok`, an explanatory report, and for a safe-division metric the wording states undefined rather than zero. |
| T-811 | AC-806 | Two consecutive runs on identical input produce identical reports. |
| T-812 | FR-819, AC-807, AC-808 | Full run with no API key, networking disabled, replayed provider fixtures, and no DuckDB file present on disk. |
| T-813 | FR-813 | Report reproduces `semantic_version` and `used_grounding_ids` from the spec 008 response unchanged. A mismatch fails. |
| T-814 | FR-818 | Schema-invalid model output is counted as a decoding defect and surfaced by the harness rather than silently retried away. |
| T-815 | FR-804a | The verifier exposes no flag, config key, or environment variable that disables the digit rule. Asserted against the public surface of `report_check`, so FR-807 and FR-808 cannot be bypassed at runtime. |
| T-816 | FR-802b, FR-802c | Assemble a prompt for a result containing `customers.name` and `customers.annual_income`; assert the serialized prompt contains neither value, does contain their labels, and that local rendering restores both. Repeat with `AVG(annual_income)` to assert aggregates pass through. |
