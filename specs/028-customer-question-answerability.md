# 028 — Make every customer self-service preset question answerable

## Problem

The customer self-service workspace (`apps/web/src/CustomerWorkspace.tsx`, spec 024) ships 5
simulated customers with 6 fixed preset questions each (30 total, plus 3 shared out-of-scope
demo questions). Live inspection of the schema (`knowledge/bank-workshop`) and the customer-scope
allowlist (`src/cerebro/customer_scope.py`) found that most resolve correctly, but several ask
for data or a computation the grounding does not support:

- **"Next loan payment amount/due date"** (User-2, User-4): `table.loan_payments` only records
  past payment events (`payment_id, loan_id, payment_date, amount_paid, principal_component,
  interest_component, late_payment_flag`) — there is no forward schedule or due-date table. This
  is unanswerable for any customer, unconditionally, not a retrieval or SQL-generation failure.
- **"Loan maturity date"** (User-2): `table.loans` has `start_date`/`term_months` but no
  `maturity_date` column and no grounded rule telling the model how to derive one, so the model
  has to invent the arithmetic ad hoc.
- **"Remaining/outstanding loan balance"** (User-2, User-4): no stored balance column and no
  metric naming `loan_amount − principal paid so far`; nothing in grounding points the model at
  the two facts it needs to combine.
- **"Longest consecutive on-time payment streak"** (User-4): requires gaps-and-islands
  window-function SQL with no supporting grounding at all — a high-failure-risk shape, not one a
  small grounding addition can fix.
- **"Money transferred between my own accounts"** (User-5): `table.transactions` has no
  counterparty/destination-account column, so inter-account transfers cannot be distinguished
  from external payments — undecidable with the current schema, not a bug.

## Goal

Every one of the 30 customer preset questions resolves to a real answer (a concrete value,
including a legitimate "0"/"never" for questions with no matching rows — that is a correct
answer, not a failure) when asked through the deployed customer chat path, verified by an actual
run against a live server, not by inspection alone.

## Non-Goals

- Extending `src/cerebro/models.py`'s `MetricCandidate`/`AggregateMeasure`/`BusinessRuleCandidate`
  schemas to support new measure/rule kinds (e.g. a "difference" measure). The two new grounding
  objects added here (FR-2, FR-3) are deliberately modeled to fit the existing closed enums
  (`AggregateMeasure.aggregation`, `BusinessRuleCandidate.rule_kind`/`output_type`) with zero
  schema changes.
- A forward loan-payment schedule, due-date table, or transaction counterparty/destination
  column — these require new source data that does not exist in `knowledge/bank-workshop`'s
  underlying DuckDB tables; out of scope for a knowledge-bundle-only change.
- Any change to `SQLGuardrail`, `CUSTOMER_ROW_FILTER_TABLES`, `CUSTOMER_SCOPE_BLOCKED_TABLES`, or
  the row-level security boundary — untouched by this spec.
- Any change to the internal/analyst preset questions (`apps/web/src/presetQuestions.json`,
  spec 018) — this spec is scoped to `CustomerWorkspace.tsx` only.
- A permanent, checked-in live-validation script for customer questions (unlike spec 018's
  `scripts/validate_preset_questions.py`, which targets the analyst `/api/chat` path with no
  `customer_id`). Verification here is a one-off manual live run against the dev server
  (see Test Design), since `customer_id`-scoped validation isn't this spec's reusable tooling to
  build.

## Functional Requirements

- FR-1: `apps/web/src/CustomerWorkspace.tsx`'s `CUSTOMER_USERS` question lists MUST replace the
  5 unanswerable/overly-complex questions identified above with rewordings that the existing
  schema and grounding fully support (see the table below). No other preset question text
  changes.
- FR-2: `knowledge/bank-workshop/metrics/customer-loan-principal-paid-total.md` MUST be added as
  a new `Metric` object, structurally identical in shape to
  `metrics/customer-loan-repayment-total.md` (`AggregateMeasure`, `aggregation: sum`,
  `source: {table: table.loan_payments, column: principal_component}`,
  `entity: entity.customer`, dependency chain `table.customers` → `table.loans` →
  `table.loan_payments`), so the model has a named, reliable way to compute "total principal
  paid on a loan so far."
- FR-3: `knowledge/bank-workshop/rules/loan-maturity-date.md` MUST be added as a new
  `Business Rule` object with `rule_kind: time_anchor`, `output_type: date`,
  `entity: entity.loan`, `dependencies: [table.loans]`, and logic text stating that loan
  maturity date is `loans.start_date` plus `loans.term_months` months — modeled on the existing
  `rules/relative-time-anchor.md` shape.
- FR-4: `src/cerebro/customer_scope.py` MUST add `"metric.customer-loan-principal-paid-total"` to
  `CUSTOMER_SCOPE_METRIC_IDS` and `"rule.loan-maturity-date"` to `CUSTOMER_SCOPE_RULE_IDS`, so the
  two new objects are visible to customer-scoped grounding (`grounding(...,
  allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS)`) and to `GET /api/graph?scope=customer`.
- FR-5: `cerebro validate` MUST pass against the modified bundle with no new warnings/errors
  attributable to the two new files.

## Question changes (FR-1)

| User | Old question | New question | Reason |
|---|---|---|---|
| User-2 | "Khoản thanh toán tiếp theo của tôi là bao nhiêu và khi nào đến hạn?" | "Lần thanh toán gần nhất của tôi là khi nào và tôi đã trả bao nhiêu?" | No forward payment schedule exists; last-payment lookup is fully supported. |
| User-4 | "Khi nào tôi phải thanh toán khoản vay tiếp theo?" | "Lần thanh toán gần nhất của khoản vay của tôi là khi nào?" | Same reason. |
| User-4 | "Tôi đã thanh toán đúng hạn bao nhiêu kỳ liên tiếp?" | "Tôi đã thanh toán trễ hạn bao nhiêu kỳ trong tổng số các kỳ đã thanh toán?" | Drops the gaps-and-islands "consecutive streak" requirement in favor of a simple aggregate/filter over `late_payment_flag`. |
| User-5 | "Tôi đã chuyển bao nhiêu tiền giữa các tài khoản của mình trong 30 ngày qua?" | "Tôi đã chi tiêu bao nhiêu trong 30 ngày qua theo từng tài khoản?" | No counterparty/destination column exists to identify inter-account transfers; per-account spend aggregation is fully supported. |

The two remaining flagged questions ("Khoản vay của tôi còn nợ bao nhiêu?" / User-2, "Số dư gốc
còn lại của khoản vay của tôi là bao nhiêu?" / User-4, and "Khoản vay của tôi sẽ đáo hạn khi
nào?" / User-2) keep their existing wording — FR-2/FR-3 make them answerable instead of replacing
them.

## Acceptance Criteria

- AC-1: `apps/web/src/CustomerWorkspace.tsx` contains the 4 reworded questions above verbatim and
  no longer contains any of their old wordings.
- AC-2: a customer-scoped grounding call (`customer_id` set) includes
  `metric.customer-loan-principal-paid-total` and `rule.loan-maturity-date` in its retrieved
  evidence/object set when the question text plausibly relates to loan balance or maturity
  (reuse the existing grounding-retrieval test pattern in `tests/test_customer_scope.py` /
  `tests/test_retrieval.py`).
- AC-3: `"metric.customer-loan-principal-paid-total"` and `"rule.loan-maturity-date"` are both
  members of `CUSTOMER_SCOPE_OBJECT_IDS`.
- AC-4: `cerebro validate` exits 0 against the modified `knowledge/bank-workshop` bundle.
- AC-5: a manual live run of all 30 customer preset questions (5 users × 6 questions) against a
  running dev server, via `POST /api/chat` with each user's `customer_id`, shows every question
  resolving with `status: "answered"` and either a non-empty result or a legitimate empty-result
  answer (e.g. "no late payments found") — never a decline, clarification, or SQL error, except
  for the 3 `OUT_OF_SCOPE_QUESTIONS`, which are expected to decline by design (spec 024).
- AC-6: `pytest` (full suite) and `cd apps/web && npm run test && npm run build` pass.

## Test Design

- Extend `tests/test_customer_scope.py` with a new Phase-A-style test (alongside
  `test_customer_scope_object_ids_exclude_internal_only_entities_and_metrics`) asserting
  `{"metric.customer-loan-principal-paid-total", "rule.loan-maturity-date"} <=
  CUSTOMER_SCOPE_OBJECT_IDS`.
- Extend with a grounding-retrieval test asserting a loan-balance/maturity question retrieves the
  two new objects when `allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS`, following the existing
  `SemanticRetriever`/`load_validated_bundle` fixture usage already in this file.
- No `CustomerWorkspace.test.tsx` exists and none is added — no test currently asserts specific
  question strings; `npm run test`/`npm run build` remain the frontend regression gate.
- AC-5's live run is manual (one-off, via `curl`/a short throwaway script against the running
  `./scripts/dev.sh` instance), documented in the completion report with the actual
  question/answer pairs observed — not added as permanent checked-in tooling (see Non-Goals).
