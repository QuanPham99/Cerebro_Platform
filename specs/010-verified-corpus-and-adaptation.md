# 010 — Verified Corpus and Adaptation

## Problem

Spec 008 measures a baseline using a hosted model with no task-specific conditioning. Text-to-SQL against an arbitrary schema is a hard open problem; against one fixed schema of ten tables it is a learnable task. The gap between those two situations is task-specific data, and there is none.

`evaluation/golden-questions.yaml` holds ten questions with `required_ids` and no SQL labels. Hand-authoring hundreds of labelled pairs is slow, and produces labels whose correctness nobody can demonstrate.

The bundle solves this. Ten table documents give columns and types, eleven relationship documents give legal join predicates with cardinality, four metric documents give governed formulas, and nine concept documents give the warnings that separate a correct query from a plausible one. A query composed from those declarations is correct by construction, and executable against 5.87 million real rows for confirmation.

This makes the semantic layer do two jobs from one artifact: grounding at runtime, and supervision at build time.

## Goal

Generate a verified natural-language-to-`(QueryPlan, SQL)` corpus deterministically from the bundle, then use it to lift spec 008 above its own baseline under a measurement protocol fixed in advance.

## Two consumers, one corpus

The corpus is the deliverable. How it conditions the model is a separate, later decision:

| Consumer | Mechanism | Requires |
|---|---|---|
| **Few-shot retrieval** (primary) | retrieve k nearest verified exemplars per question, inject into both stage prompts | nothing beyond the spec 008 key |
| **Weight adaptation** (conditional) | supervised fine-tune producing one adapter for both stages | a key that permits fine-tuning, plus training compute |

Few-shot is primary because it is available immediately, adds no infrastructure, and reuses ranking machinery the repo already has. Weight adaptation is attempted only if the organizer key permits it and few-shot leaves measurable headroom. Corpus work is unconditionally useful; neither consumer is on the critical path.

## Non-Goals

- Reinforcement learning from execution reward.
- Pre-training or full-parameter training.
- Improving the bundle. It remains read-only input owned by another workstream.
- Generalization to other schemas. Both consumers are specific to `bank-workshop`.
- Changing the spec 008 or spec 009 contracts. Adaptation sits behind the existing provider interface.
- Self-hosting a model.

## Functional Requirements

### Template enumeration

- FR-900: Templates are enumerated from the bundle, never hand-written per question. Sources are the four metrics, ten tables, eleven declared relationships, and per-column dimension candidates.
- FR-901: A column qualifies as a group-by dimension only when its grounding classification is `public` or `internal` and its distinct count is at or below a cardinality cap. Distinct counts come from build-time query against the materialized DuckDB. This is the only sanctioned use of value discovery in the project and it never touches a `restricted` or `confidential` column.
- FR-902: Join predicates come only from `relationship.*` declarations. Multi-hop paths are composed by traversing declared edges, so a generated query cannot contain an undeclared predicate. The two-hop `transactions` to `branches` route through `accounts` is produced by traversal, not by special-casing.
- FR-903: Metric-bearing templates embed the governed `formula` verbatim, matching spec 008 FR-711. The label therefore teaches exactly the behaviour the runtime checker enforces.
- FR-904: Every example carries both labels: the `QueryPlan` JSON and the SQL string. One example serves both spec 008 stages, whichever consumer uses it.
- FR-905: Semantic obligations are part of the label, not left implicit. `plan.warnings_addressed` is populated from the warnings of every bundle object the template uses, and any template with a relative time window anchors on `MAX(<time column>)` rather than `CURRENT_DATE`.
- FR-906: A template projecting a `restricted` or `confidential` column must carry a row limit at or below the spec 008 FR-713 disclosure cap, and must record the disclosure in its plan the same way the runtime agent would.
- FR-907: Templates requiring business meaning absent from the bundle are not generated. Direction semantics for `txn_type` are the current instance: filters on specific values are generated, inflow and outflow aggregation is not, because no declaration supports it.

### Verification

- FR-908: Every generated SQL executes read-only against the materialized DuckDB. Examples that error are discarded and the error recorded.
- FR-909: Every generated example passes through the spec 008 `selfcheck` module, evaluated against the grounding packet actually retrieved for its paraphrased question. Failures are discarded. A corpus may not carry behaviour the runtime gate rejects, whether it is used as an exemplar or as a training label.
- FR-910: Examples returning zero rows are retained only up to a quota, so neither consumer learns that empty results are a normal target.
- FR-911: Deduplicate by `sqlglot`-normalized AST rather than by string, so alias and whitespace variants collapse.

### Paraphrase

- FR-912: Each verified SQL receives several natural-language questions, generated through the spec 008 provider. Paraphrase is corpus construction rather than inference, but it consumes the same quota and must therefore be batched, cached, and resumable so an interrupted run does not repeat completed work.
- FR-913: The SQL label is fixed before paraphrasing and is never regenerated from a paraphrase. A paraphrase that drifts in meaning corrupts one example; a paraphrase trusted to regenerate labels corrupts the corpus.
- FR-914: Record per-example provenance: template id, bundle `semantic_version`, paraphrase model, and verification outcomes.
- FR-915: Paraphrase prompts carry template metadata and column names, never source rows and never values from `restricted` or `confidential` columns. Spec 008 FR-703a applies unchanged to this offline path.

### Splits and leakage

- FR-916: The ten golden questions are never used to condition the model, by either consumer. They are the held-out set and the measurement instrument.
- FR-917: Split by template, not by example. All paraphrases of one SQL land on the same side. Splitting by example lets a paraphrase of a conditioned query appear in test and inflates the reported score.
- FR-918: Exclude any example whose normalized SQL AST matches a golden question's expected query. Matching on question text is insufficient, because a paraphrase can differ in wording while targeting the same query.
- FR-919: For few-shot retrieval, leakage occurs at retrieval time rather than at training time. When evaluating, the exemplar bank exposed to a question must exclude every example sharing that question's template, and every example excluded by FR-918. Retrieving the answer is not few-shot learning.
- FR-920: Publish corpus composition: counts by metric, by table, by join depth, and by template family, so gaps are visible before conditioning rather than after.

### Few-shot retrieval

- FR-921: Build an exemplar index over corpus questions, reusing the spec 005 ranking approach: lexical scoring, optional embeddings, reciprocal rank fusion. No new retrieval mechanism is introduced.
- FR-922: At inference, retrieve at most k exemplars and inject them into both spec 008 stages. k is configuration. Exemplars are shown as `(question, plan)` for stage 1 and `(question, plan, sql)` for stage 2.
- FR-923: Exemplar injection is bounded by prompt budget. When k exemplars would exceed the budget, drop the lowest-ranked rather than truncating an exemplar, since a half-shown example teaches malformed output.
- FR-924: Exemplar retrieval is deterministic for a fixed corpus, question, and k.

### Weight adaptation, conditional

- FR-925: Attempted only when the organizer key permits fine-tuning. Produces one adapter with two tasks distinguished by instruction prefix, mirroring the spec 008 stages.
- FR-926: Only verified text pairs are submitted. No source row, and no value from a `restricted` or `confidential` column, is uploaded.
- FR-927: The adapter is reached through the same provider adapter with no contract change and no prompt change beyond exemplar removal.
- FR-928: Record the adaptation configuration as a committed artifact: base model, method, hyperparameters, corpus revision, and seed.

### Measurement

- FR-929: The comparison protocol is fixed before any conditioning. Identical golden set, identical grounding packets, identical temperature, identical prompts apart from the conditioning under test, identical self-check. Exactly one variable changes per run.
- FR-930: Report per-question status transitions against the spec 008 baseline, not an aggregate alone. A regression on any single question is reported even when the aggregate improves.
- FR-931: Report the structured-output defect rate from spec 008 FR-703 separately for every run. A model often improves mainly by emitting valid structure more often, which is a different achievement from improved semantics and must not be reported as the latter.
- FR-932: Report quota consumed per run. Few-shot raises prompt size, so an accuracy gain that triples cost is a result to state, not to hide.
- FR-933: If no conditioning beats baseline under FR-929, the baseline ships. Both consumers are additive and never on the critical path.

## Acceptance Criteria

- AC-900: No generated SQL contains a join predicate absent from the declared relationships.
- AC-901: Every retained example passes spec 008 `selfcheck` against its own retrieved grounding.
- AC-902: No golden question id appears in any conditioning path. Asserted by id, not by text similarity.
- AC-903: No paraphrase of a conditioned SQL appears in the test split. Asserted by template id disjointness.
- AC-904: During golden-set evaluation, no retrieved exemplar shares a template with the question under test, and none is AST-equivalent to it.
- AC-905: The spec 008 AC-708 baseline artifact exists before any conditioning run. Absent baseline blocks the step.
- AC-906: Each comparison run differs from baseline by exactly one variable. Prompt template, grounding, temperature, k, and checker version are recorded in every artifact and asserted equal where they should be.
- AC-907: A per-question transition table is published, listing improvements and regressions, alongside defect rate and quota consumed.
- AC-908: No source row and no sensitive value leaves the machine on any path, including paraphrase and adaptation upload. Asserted by scanning outbound payload artifacts for values drawn from `restricted` and `confidential` columns.
- AC-909: With conditioning active, the full spec 008 and spec 009 test suites still pass. Conditioning changes prompts and weights, not contracts.
- AC-910: The corpus is regenerable from the bundle, the materialized DuckDB, the recorded paraphrase cache, and the recorded seed alone.

## Edge Cases

- Metric whose only viable dimensions are sensitive, leaving no legal template.
- Multi-hop join causing fan-out, where the template must aggregate before comparing or be discarded. `concept.bad-debt` names this case for loans against employee headcount.
- Column with distinct count of one, which is a useless dimension.
- Two templates collapsing to the same query after AST normalization.
- Paraphrase run interrupted mid-way, requiring resume without duplicate quota spend.
- Exemplar retrieval returning k results all from one template family, narrowing rather than broadening guidance.
- Prompt budget exceeded by exemplars plus a large grounding packet.
- Bundle changing mid-window, making `semantic_version` differ across examples.
- Conditioning improving structural validity while semantic accuracy is flat, which FR-931 exists to expose.

## Interfaces / Contracts

Artifacts, all under gitignored paths except committed configuration:

```text
evaluation/golden-questions.yaml         existing, held out, never used to condition
data/corpus/corpus.jsonl                 generated corpus
data/corpus/manifest.json                composition, provenance, seed
data/corpus/paraphrase-cache.jsonl       resumable paraphrase results
artifacts/baseline-<timestamp>.json       produced by spec 008 AC-708
artifacts/fewshot-eval-<timestamp>.json   produced by FR-929
artifacts/adapter-eval-<timestamp>.json   produced by FR-929, conditional path
config/corpus.yaml                        committed generation configuration
config/adaptation.yaml                    committed adaptation configuration
```

Corpus record:

```json
{
  "id": "tpl.fraud-rate-by-dim.card_type#p2",
  "template_id": "tpl.fraud-rate-by-dim.card_type",
  "task": "plan | sql",
  "question": "What is the fraud rate for each card type?",
  "plan": { "...": "QueryPlan from spec 008" },
  "sql": "SELECT ...",
  "semantic_version": "0.1.0",
  "paraphrase_model": "<provider model id>",
  "verification": {
    "executed": true,
    "row_count": 4,
    "selfcheck": "passed",
    "violations": []
  }
}
```

Modules:

| Module | Responsibility | Needs the provider? |
|---|---|---|
| `scripts/gen_templates.py` | FR-900 to FR-907 | no |
| `scripts/verify_corpus.py` | FR-908 to FR-911 | no |
| `scripts/paraphrase.py` | FR-912 to FR-915, cached and resumable | yes |
| `scripts/split_corpus.py` | FR-916 to FR-920 | no |
| `src/cerebro/exemplars.py` | FR-921 to FR-924 | no |
| `scripts/adapt.py` | FR-925 to FR-928, conditional | yes |
| `src/cerebro/evaluation.py` | extended for FR-929 to FR-932 | replayable |

Everything except paraphrase and adaptation is deterministic and testable without the provider.

## Constraints

- Generation, verification, splitting, and exemplar retrieval are deterministic given bundle, database, cache, and seed.
- Paraphrase and adaptation consume the shared organizer quota. Both must be cached and resumable.
- The bundle is not modified.
- Spec 008 and spec 009 contracts are not modified.
- Exemplar retrieval reuses the spec 005 ranking approach rather than introducing a second mechanism.

## Assumptions

- The bundle is stable across the window. `semantic_version` is recorded per example so drift is detectable rather than silent.
- Corpus size is set by baseline error analysis, not chosen in advance.
- Paraphrase through the organizer provider is acceptable because it produces questions and never labels. Labels come from the bundle.

## Open Questions

- Whether the organizer key permits fine-tuning. This decides whether FR-925 to FR-928 execute at all. Few-shot proceeds regardless.
- How many exemplars balance guidance against prompt cost. Determined by FR-932 measurement, not chosen in advance.
- How many paraphrases per SQL balance diversity against overfitting to phrasing.
- Whether reinforcement learning with an execution-based reward is worth adding later. Deferred and out of the current window.
- Whether the semantic-layer owner will declare `txn_type` direction semantics. Until then FR-907 excludes those templates, and the corpus has a known blind spot that should be stated in the demo rather than hidden.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-900 | FR-900, FR-902, AC-900 | Corpus contains the two-hop `transactions` to `branches` path composed from declared edges; no example contains an undeclared predicate. |
| T-901 | FR-901 | Dimension candidate selection excludes all `restricted` and `confidential` columns and any column above the cardinality cap. |
| T-902 | FR-903, FR-905 | Metric templates contain the governed formula unmodified; time-window templates contain `MAX(` over the time column and neither `CURRENT_DATE` nor `NOW()`. |
| T-903 | FR-904 | Every retained example carries both a schema-valid `QueryPlan` and a SQL string. |
| T-904 | FR-906 | A template projecting `customers.name` carries a row limit at or below the disclosure cap and records the disclosure in its plan. |
| T-905 | FR-907 | No generated template aggregates transaction direction. |
| T-906 | FR-908, FR-909, AC-901 | Every retained example executed successfully and passed `selfcheck`. A deliberately corrupted example is discarded with a recorded reason. |
| T-907 | FR-910, FR-911 | Zero-row quota enforced; alias-only SQL variants collapse to one example. |
| T-908 | FR-912, FR-913 | Paraphrase run is resumable: interrupting and restarting produces no duplicate provider calls and no changed labels. Regenerating a label from a paraphrase is unreachable in code. |
| T-909 | FR-914, FR-920 | Manifest reports per-example provenance and counts by metric, table, join depth, and template family. |
| T-910 | FR-915, FR-926, AC-908 | Outbound payload artifacts for both paraphrase and adaptation scanned for values from `restricted` and `confidential` columns; any hit fails the build. |
| T-911 | FR-916 to FR-918, AC-902, AC-903 | Train and test template id sets disjoint; no golden question id conditioned; an example whose normalized AST matches a golden query is excluded. |
| T-912 | FR-919, AC-904 | During golden-set evaluation, assert no retrieved exemplar shares the question's template or is AST-equivalent to it. |
| T-913 | FR-921 to FR-924 | Exemplar retrieval is deterministic for fixed corpus, question, and k; exceeding prompt budget drops lowest-ranked exemplars whole rather than truncating one. |
| T-914 | FR-925, FR-927, FR-928, AC-909 | Conditional path: spec 008 and spec 009 suites pass with the adapter active; adaptation configuration recorded as an artifact. |
| T-915 | FR-929, AC-905, AC-906 | Conditioning is blocked without a baseline artifact. Comparison run asserts exactly one variable differs, with prompt, grounding, temperature, k, and checker version recorded in both artifacts. |
| T-916 | FR-930 to FR-933, AC-907 | Transition table lists per-question improvements and regressions; defect rate and quota consumed reported per run; when no run beats baseline the release selection returns baseline. |
| T-917 | AC-910 | Regenerating the corpus from bundle, database, paraphrase cache, and recorded seed reproduces it byte-for-byte. |
