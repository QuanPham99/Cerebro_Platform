# Feature: Chat grounding and SQL-validator precision fixes

## Problem

Live evaluation of the governed chat agent (`ChatOrchestrator` in `src/cerebro/chat.py`, backed
by `SemanticRetriever` in `src/cerebro/retrieval.py`) against the bank-workshop golden bundle
surfaced two reproducible defects, both confirmed by direct calls into the affected functions
against the real bundle (not just live-agent transcripts):

1. **SQL safety validator rejects a standard `ORDER BY <alias>` pattern.** `SQLGuardrail.validate`
   (`src/cerebro/chat.py`) walks every `exp.Column` node in the parsed SQL and requires each one to
   resolve to a physical catalog column, with an existing exemption only for CTE-qualified derived
   aliases. A flat query with a window-function column aliased in the `SELECT` list and referenced
   by that alias in `ORDER BY` (e.g. `... AS branch_fraud_exposure ... ORDER BY
   branch_fraud_exposure`) is rejected with `Column is not in the approved catalog:
   branch_fraud_exposure`, even though the query is well-formed, uses only approved joins, and
   never reads an unapproved column. This blocked a correct, well-grounded agent answer to
   "Which branches have the highest fraud exposure, and what drives that metric?".

2. **Grounding retrieval caps every `business_rule` candidate to exactly one, even when a second,
   genuinely relevant rule is available.** `SemanticRetriever._progressive_grounding_ids`
   (`src/cerebro/retrieval.py`) selects at most `limits["business_rule"] == 1` business rule
   object to include with full content in `GroundingResponse.rules`. When a question's top-ranked
   non-rule object (e.g. a metric) claims the sole `requested_kinds` slot, `business_rule` is
   excluded entirely from the seed-selection loop even if a rule scores well by
   `_kind_match_score` against the question. The rule's id still surfaces in the lightweight
   `ranking_evidence` list (and therefore in the chat response's `evidence_ids`), but its actual
   definition (logic, thresholds, dependencies) is never included in the grounding handed to the
   LLM. The agent then — correctly, given what it received — reports the rule as "referenced in
   ranking evidence but not defined in the supplied grounding" and asks for clarification, even
   though `knowledge/bank-workshop/rules/high-value-multichannel-customer.md` has a complete
   definition in the active bundle. Confirmed via direct `SemanticRetriever.grounding(...)` calls:
   for "List customers who qualify as high-value multichannel customers along with their net cash
   flow.", `grounding.rules == []` even though `rule.high-value-multichannel-customer` scores
   `1.023` (clearly positive) via `_kind_match_score` and appears in `ranking_evidence`.

## Goal

- The SQL validator accepts syntactically standard `ORDER BY`/`HAVING` references to a
  `SELECT`-list output alias, without weakening its existing column-catalog/classification checks
  for any other column reference.
- Grounding retrieval includes a second (and, bounded, third) `business_rule` object's full
  definition when it has genuine lexical/vector name-term overlap with the question, even when the
  coarse keyword-based intent classification (`rule_intent`, `requested_kinds`) would otherwise
  exclude the `business_rule` kind entirely.

## Non-Goals

- Fixing the separate, previously-diagnosed issue where "active accounts" fails to retrieve
  `table.accounts`/`entity.account`. Root-caused during this work as a token/stemming mismatch
  (`_tokens` has no plural stemming, so the query term "accounts" never overlaps the entity's
  singular name "Account") — a different, broader-blast-radius change (global scoring/tokenization)
  than this spec's scope. Left for a follow-up spec.
- Raising retrieval limits for any kind other than `business_rule` (e.g. `metric`, `dimension`,
  `entity`, `physical_table`). No concretely reproduced failure requires it, and doing so
  indiscriminately was tried during investigation and produced unwanted noise (zero-scored,
  topically-unrelated same-kind objects and their full dependency closures being pulled into the
  grounding context).
- Investigating the 3 preset test questions that still time out at 400s client-side even without
  request pileup. Plausibly related (repeated internal retry after a validator rejection costs a
  full LLM round trip each time) but not confirmed; out of scope until fix #1 is verified against
  those specific questions.
- Rewriting `_progressive_grounding_ids`'s keyword-based intent classification in general.

## Functional Requirements

- FR-1: `SQLGuardrail.validate` MUST accept an unqualified column reference inside `ORDER BY` or
  `HAVING` when its name matches an alias defined in the current `SELECT`'s own output list, even
  when that name is not a physical catalog column.
- FR-2: `SQLGuardrail.validate` MUST continue to reject an unqualified column reference that
  matches neither a physical catalog column for the query's tables nor a current-`SELECT`-list
  output alias.
- FR-3: `SQLGuardrail.validate` MUST continue to reject a qualified (table-prefixed) reference to a
  name that is not a real physical column, and MUST continue to enforce `restricted`/`confidential`
  classification checks unchanged for every column that does resolve to a physical catalog column.
- FR-4: `SemanticRetriever._progressive_grounding_ids` MUST include a `business_rule` candidate in
  the selected seed set whenever it has a *strong* `_kind_match_score` (>= 1.0 — full coverage of
  either the rule's own name terms or the question's terms, not an incidental single shared word)
  against the question, even if the coarse `rule_intent` keyword check is `False` or
  `business_rule` is not in the computed `requested_kinds` set.
- FR-5: When more than one `business_rule` candidate has a strong (>= 1.0) `_kind_match_score`, up
  to 3 total business rules (previously 1) MUST be included in the selected seed set, ranked by
  score.
- FR-6: A `business_rule` candidate scoring below the strong-match threshold MUST NOT be included
  in the selected seed set merely because the cap was raised or another same-kind candidate scored
  strongly — i.e. the existing "always take the single top-ranked candidate for a passed kind"
  behavior is preserved as a floor, and every additional candidate beyond the first requires its
  own >= 1.0 score. A weak positive score (e.g. one shared incidental word) is deliberately not
  enough — an earlier iteration of this fix used `score > 0` and it regressed an existing bound
  (`tests/test_semantic_profile.py::test_progressive_grounding_is_bounded_and_semantic_oracle_is_kind_aware`)
  by pulling in `rule.branch-fraud-escalation` for "fraud rate by card type" on the single shared
  word "fraud".
- FR-7: Behavior for every other profile kind (`metric`, `dimension`, `entity`, `physical_table`,
  `relationship`, `policy`, `legacy_concept`) MUST be unchanged.

## Acceptance Criteria

- AC-1: `SQLGuardrail(bundle).validate(sql)` succeeds (returns SQL, does not raise) for a query
  that joins `branches`→`accounts`→`cards`→`card_transactions` via approved relationships,
  computes a window-function column aliased `branch_fraud_exposure`, and orders by that alias —
  the exact query the live agent generated for "Which branches have the highest fraud exposure,
  and what drives that metric?".
- AC-2: `SQLGuardrail(bundle).validate(...)` still raises `SQLSafetyError` for a query that
  references a genuinely unknown, non-aliased column name (regression: existing
  `test_sql_guardrail_allows_aggregates_and_blocks_sensitive_or_writes` continues to pass
  unmodified).
- AC-3: `SQLGuardrail(bundle).validate(...)` still raises `SQLSafetyError` for a query that reads a
  `restricted` column, and for one that reads a `confidential` column outside an aggregate
  (regression, no new test needed — covered by existing suite).
- AC-4: `SemanticRetriever(bundle).grounding("List customers who qualify as high-value
  multichannel customers along with their net cash flow.").rules` contains an object with
  `id == "rule.high-value-multichannel-customer"`.
- AC-5: For the same question, `grounding.rules` does NOT contain
  `rule.delinquent-customer-support-priority` or `rule.employee-risk-portfolio-assignment` (both
  score 0 for this question — regression against the noise this fix must not introduce).
- AC-6: `SemanticRetriever(bundle).grounding("How many customers are there by gender?").rules ==
  []` (regression: a question with no rule-relevant terms still returns no rules).
- AC-7: `SemanticRetriever(bundle).grounding("fraud rate by card type")` keeps its total selected
  object count (entities + dimensions + metrics + rules + tables + joins + concepts) at <= 10,
  matching the pre-existing bound asserted by
  `tests/test_semantic_profile.py::test_progressive_grounding_is_bounded_and_semantic_oracle_is_kind_aware`,
  and `grounding.rules == []` for that question (`rule.branch-fraud-escalation` scores 0.583 —
  below the strong-match threshold — so it correctly stays excluded).
- AC-8: `SemanticRetriever(bundle).grounding("What is the total number of active accounts?").rules
  == []`, unchanged from pre-fix behavior. `rule.active-customer` scores 0.750 for this question —
  below the strong-match threshold — so this case is neither newly broken nor newly fixed by this
  spec; it remains the documented "active accounts" gap (Non-Goals), now precisely bounded by an
  explicit score.

## Edge Cases

- EC-1: A query with zero `ORDER BY`/`HAVING` clauses is unaffected (no output aliases are ever
  consulted for column resolution outside those clauses).
- EC-2: An `ORDER BY` reference that happens to share a name with both a real physical column and
  a `SELECT`-list alias resolves via the physical-column path first (the alias exemption is only
  reached when `classifications` is already empty), so no existing classification/restriction
  check is weakened.
- EC-3: A question where zero business rules score positively (e.g. "How many customers are there
  by gender?") behaves exactly as before the fix — either zero or one rule selected per the
  pre-existing `requested_kinds`/`rule_intent` gates.
- EC-4: A question where three or more business rules score >= 1.0 is capped at the 3
  highest-scoring ones (FR-5), not an unbounded number.
- EC-5: A business rule sharing only one incidental, low-signal word with the question (e.g.
  "fraud" appearing in both the metric name and an unrelated rule's name) scores below 1.0 and is
  correctly excluded — this is what keeps the fix from reintroducing the noise problem discovered
  during implementation (AC-7).

## Interfaces / Contracts

- No public API/schema change. `GroundingResponse.rules` and `ChatResponse` shapes are unchanged;
  only which objects populate `rules` for a given question, and which SQL passes validation,
  change.

## Constraints

- Must not weaken any existing `restricted`/`confidential` column check.
- Must not change behavior for any profile kind other than `business_rule` (FR-7).
- Must not require a data/OKF bundle change — code-only fix.

## Assumptions

- `sqlglot`'s `Select.selects` reliably yields `exp.Alias` nodes for every aliased `SELECT`
  projection and plain expressions otherwise; confirmed empirically against the reproduction SQL.
- Raising `business_rule`'s cap from 1 to 3 is a safe, bounded increase; no reproduced case needs
  more than 2 simultaneously, 3 leaves headroom without materially growing prompt size.
- A `_kind_match_score` of >= 1.0 is a reasonable operational definition of "strong, name-covering
  match" (in practice: full coverage of the rule's own name terms, or an exact name/query-term
  match triggering the `+2.0` bonus in `_kind_match_score`) versus a merely incidental single
  shared word (e.g. "fraud" alone scores ~0.58 against a 3-term rule name in the reproduced
  regression) — verified empirically against the bundle, not assumed from formula inspection
  alone.

## Open Questions

- None blocking. The "active accounts" table/entity gap and the 3 still-timing-out preset
  questions are recorded as non-goals for a follow-up, not open questions against this spec's
  scope.

## Test Design

| Test ID | Requirement | Level | Scenario | Expected Result |
|---|---|---|---|---|
| T-01 | FR-1/AC-1 | Unit | `SQLGuardrail.validate` on the branch-fraud-exposure window-function query with `ORDER BY` alias | Returns validated SQL, no exception |
| T-02 | FR-2/FR-3/AC-2 | Unit | Existing `test_sql_guardrail_allows_aggregates_and_blocks_sensitive_or_writes` | Still passes unmodified |
| T-03 | FR-4/FR-5/AC-4 | Unit | `SemanticRetriever.grounding(...)` on the high-value-multichannel-customer question | `rule.high-value-multichannel-customer` present in `grounding.rules` |
| T-04 | FR-6/AC-5 | Unit | Same question | `rule.delinquent-customer-support-priority` and `rule.employee-risk-portfolio-assignment` absent from `grounding.rules` |
| T-05 | FR-7/AC-6 | Unit | `SemanticRetriever.grounding("How many customers are there by gender?")` | `grounding.rules == []` |
| T-06 | EC-5/AC-7 | Unit | `SemanticRetriever.grounding("fraud rate by card type")` | `grounding.rules == []`; total selected object count <= 10 |
| T-07 | AC-8 | Unit | `SemanticRetriever.grounding("What is the total number of active accounts?")` | `grounding.rules == []` (unchanged from pre-fix baseline) |
| T-08 | Regression | Integration | Full existing `tests/test_chat.py` and `tests/test_retrieval_api_mcp.py` suites | All pass |
| T-09 | Regression | Integration | Full repository `pytest` run, including `tests/test_semantic_profile.py`'s pre-existing bounded-grounding assertion | All pass, no new failures |

Categories evaluated: Happy path (T-01, T-03), Validation (T-02), Edge cases (T-04, T-06),
Error handling (T-02 continues to raise where expected), Regression (T-02, T-05, T-07, T-08),
Integration (T-07, T-08), Contract/schema (N/A — no schema change), Security (T-02/T-03 in
existing suite cover restricted/confidential column blocking — unchanged), Performance (N/A — not
addressed by this spec; see Non-Goals).
