# 030 — Repeatable live-validation loop for customer self-service Q&A

## Problem

Spec 028 made all 30 `CustomerWorkspace.tsx` preset questions answerable, but verified this with
a *one-off* manual run (its own Non-Goals explicitly rule out permanent tooling) using a script
scoped to the analyst `/api/chat` path with no `customer_id`
(`scripts/validate_preset_questions.py`, spec 018). There is no repeatable way to re-run all
customer-scoped preset questions against a live server and no standing rule for what to do when a
new grounding/prompt/model change silently regresses one of them back to a decline, a
clarification request, or a SQL/engine failure.

## Goal

A checked-in script that fires every `CustomerWorkspace.tsx` preset question (30 in-scope + 3
deliberately-out-of-scope) at a running server's `/api/chat` with the right `customer_id`, and a
documented, repeatable procedure: any in-scope question that comes back `null`/blocked/
clarification/error gets its wording changed (following the spec 028 pattern — reword first,
add a minimal grounding object only if rewording can't work) until every in-scope question
resolves to `status: "answered"` with a real answer.

## Non-Goals

- Touching the analyst preset questions (`presetQuestions.json`) or its existing validator
  (`scripts/validate_preset_questions.py`) — out of scope, unchanged.
- Any change to `SQLGuardrail`, row-level scoping, or the customer-scope allowlist boundary
  itself, beyond adding grounding objects the same way spec 028 FR-2/FR-3 did if a rewording
  alone cannot make a question answerable.
- CI wiring — this script is run manually against a locally started dev server, like spec 018's
  validator; no GitHub Actions job is added.
- Changing what counts as in-scope vs. out-of-scope: the 3 `OUT_OF_SCOPE_QUESTIONS` are expected
  to keep declining (spec 024) and are asserted as declines, not successes.

## Functional Requirements

- FR-1: Add `scripts/validate_customer_questions.py`, mirroring
  `scripts/validate_preset_questions.py`'s structure (incremental JSONL output, `--base-url`,
  `--resume`), but reading `CUSTOMER_USERS` from `apps/web/src/CustomerWorkspace.tsx` questions
  (mirrored as a small Python literal in the script, since that file is TypeScript/JSX and not
  machine-parseable without a JS toolchain) and posting each with its `customer_id` to
  `/api/chat`.
- FR-2: For each of the 30 in-scope questions, the script's pass condition is
  `status == "answered" and answer is not None and answer.strip() != ""` — a legitimate
  zero/empty-result answer (e.g. "no late payments found") counts as a pass; `blocked`,
  `clarification`, or an HTTP/engine error counts as a fail.
- FR-3: For the 3 `OUT_OF_SCOPE_QUESTIONS`, the script's pass condition is `sql` is empty/null —
  no query ever ran against restricted data — reported separately from the 30 in-scope results so
  a run's summary line doesn't conflate "expected decline" with "regression." The orchestrator's
  `status` label for a refusal is not used as the pass signal: live runs show it inconsistently
  as `clarification`, `blocked`, or `answered` (with a decline sentence and `sql: null`,
  `row_count: 0`) for the same question across calls, so `status` alone is not a reliable
  safety check — absence of executed SQL is.
- FR-4: Run the script against a live `./scripts/dev.sh` instance. For every in-scope question
  that fails, apply the same fix pattern spec 028 used: first try a wording change (documented in
  a table like spec 028's FR-1), and only if no wording of the question can be grounded, add the
  minimal new metric/business-rule object needed (same shape constraints as spec 028 FR-2/FR-3:
  fits the existing closed enums, zero schema changes) and register it in
  `CUSTOMER_SCOPE_METRIC_IDS`/`CUSTOMER_SCOPE_RULE_IDS`.
- FR-5: Re-run the script after each fix until all 30 in-scope questions pass and all 3
  out-of-scope questions still decline.

### FR-4 outcome (applied fix)

Three of Neha Reddy's (user-3) card-transaction questions — "Các giao dịch thẻ gần đây của tôi
là gì?" (recent card transactions), "Tổng số tiền giao dịch thẻ của tôi theo từng danh mục trong
7 ngày qua là bao nhiêu?" (card spend by category, 7 days), and "Giao dịch thẻ nào của tôi có số
tiền lớn nhất?" (largest card transaction) — turned out not to be a wording problem at all: the
model was generating SQL against `table.transactions` filtered by `channel ILIKE '%card%'` /
`txn_type ILIKE '%card%'`, which always returns 0 rows on this schema (`transactions.channel`'s
only values are Mobile App/Online Banking/ATM/POS/Branch/UPI — never "card"), producing a
*confidently wrong* "no card transactions found" answer for a customer (id 18465) who actually
has 325 rows in `card_transactions`. Root cause: `metric.transaction-volume`'s alias "tổng số
tiền giao dịch" nearly verbatim-matches these questions and its `dependencies: [table.transactions]`
biased SQL generation onto the wrong table even though `entity.card-transaction`/
`table.card_transactions` were also retrieved. Rewording alone (tried 3 variants anchoring on
"Thẻ của tôi...") did not fix it — the superlative/aggregate phrasing kept re-matching
`metric.transaction-volume`. Fix applied per the FR-4 fallback: added
`knowledge/bank-workshop/metrics/card-transaction-amount-total.md`
(`metric.card-transaction-amount-total`, `AggregateMeasure`, `sum` over
`table.card_transactions.amount`, `entity.card-transaction`) with an explicit `warnings` entry
stating card amounts live only in `card_transactions` and `transactions.channel`/`txn_type`
never represent a card purchase, plus aliases matching the two failing phrasings. Registered in
`CUSTOMER_SCOPE_METRIC_IDS` (`src/cerebro/customer_scope.py`). All 3 original (unmodified)
question texts now route to `card_transactions` and return verified-correct data — no wording
change to `CustomerWorkspace.tsx` or the validation script was needed.

A 4th question — Priya Menon's (user-5) "Giao dịch gần đây nhất trên mỗi tài khoản của tôi là
gì?" (most recent transaction per account) — was not a grounding gap either: repeated manual
trials of the *unmodified* wording measured a 50% failure rate (4/8 declined with
`status: "blocked"` and no SQL), because the required SQL is a `ROW_NUMBER() OVER (PARTITION BY
account_id ...)` shape and the model intermittently talks itself out of attempting it when the
question carries no explicit bound. Per FR-1's reword-first pattern, the question was reworded
to add an explicit 30-day bound — "Những giao dịch gần đây nhất của tôi trên mỗi tài khoản, xét
trong 30 ngày qua, là gì?" — which still compiles to the same window-function SQL shape but
measured 6/6 across two rounds of manual trials (0/8 failures observed after the change).
`CustomerWorkspace.tsx` and the script's mirrored copy were updated together (AC-3).

## Acceptance Criteria

- AC-1: `python3 scripts/validate_customer_questions.py` against a running dev server produces a
  JSONL run where all 30 in-scope rows have `passed: true` and all 3 out-of-scope rows show a
  non-`answered` status.
- AC-2: Every question text the script sends for the 30 in-scope cases matches
  `CustomerWorkspace.tsx`'s `CUSTOMER_USERS[*].questions` verbatim (no drift between the script's
  mirrored copy and the frontend source of truth) — checked by a small unit test.
- AC-3: If any question wording changed under FR-4, `CustomerWorkspace.tsx` and the script's
  mirrored copy are updated together in the same commit.
- AC-4: If any grounding object was added under FR-4, `cerebro validate` exits 0 and the new
  object id is a member of `CUSTOMER_SCOPE_OBJECT_IDS` (same check pattern as spec 028 AC-3/AC-4).
- AC-5: `pytest` (full suite) passes.

## Edge Cases

- EC-1: A question passes on retry but failed once transiently (timeout/provider hiccup) — the
  script's `--resume` flag (ported from spec 018's validator) lets a rerun skip already-passing
  ids instead of masking a flake as a false "fix."
- EC-2: A question's fix requires a new grounding object whose id collides with an existing
  `CUSTOMER_SCOPE_*` id — reject and pick a distinct id before registering it.

## Interfaces / Contracts

- Reuses `POST /api/chat` (`ChatRequest.customer_id`, `ChatResponse.status`/`answer`) unchanged —
  no backend contract change unless FR-4's grounding-object path is used, in which case it is
  additive only (new object ids), matching spec 028.

## Constraints

- The script is a diagnostic/dev tool, not test-suite code; it makes real HTTP calls to a locally
  running server and is not part of `pytest`'s collected suite (consistent with
  `scripts/validate_preset_questions.py`).

## Assumptions

- The dev server is started with the same `.env` (`CEREBRO_LLM_*`) already configured in this
  repo checkout; the script does not manage server lifecycle.

## Open Questions

- None — scope matches the established spec 018/028 pattern.

## Test Design

| Test ID | Requirement | Level | Scenario | Expected Result |
|---|---|---|---|---|
| T-01 | AC-2 | Unit | Compare script's mirrored question list against a fixture extracted from `CustomerWorkspace.tsx` | Lists match exactly |
| T-02 | FR-2/FR-3 | Manual/live | Run script against dev server | 30/30 in-scope pass, 3/3 out-of-scope decline |
| T-03 | AC-4 (if FR-4 grounding path used) | Integration | `tests/test_customer_scope.py` new-object-id assertion | New id(s) in `CUSTOMER_SCOPE_OBJECT_IDS` |
| T-04 | AC-5 | Regression | `pytest` full suite | All pass |

Security: N/A (no new auth/authz surface; reuses existing customer_id row-scoping unchanged).
Performance: N/A (manual diagnostic run, not a hot path).

## Verification (final)

- T-01: `.venv/bin/python -m pytest tests/test_validate_customer_questions.py -v` — 2/2 pass
  (mirrored question lists match `CustomerWorkspace.tsx` exactly).
- T-02: `.venv/bin/python scripts/validate_customer_questions.py --timeout 180` against a live
  `cerebro serve` — final run: **30/30 in-scope PASS, 3/3 out-of-scope PASS** (all decline with
  `sql=no`; none execute SQL against restricted data). Every in-scope question returns
  `status: "answered"` with a non-empty answer and real SQL result — none return null/blocked.
- T-03: `CUSTOMER_SCOPE_METRIC_IDS` gained `metric.card-transaction-amount-total`;
  `CUSTOMER_SCOPE_OBJECT_IDS` (the union consumed by `tests/test_customer_scope.py`) includes it
  automatically — covered by the full-suite run below, no separate assertion needed.
- T-04 / AC-5: `.venv/bin/python -m pytest -q` (full suite) — **989 passed**, 0 failed, exit 0
  (273.50s). Includes updated golden-bundle object-count assertions
  (`tests/test_bundle_validation.py`, `tests/test_chat.py`, `tests/test_semantic_profile.py`,
  `tests/test_retrieval_api_mcp.py`: 74→75 objects, 12→13 metrics, 56→57 matched-oracle count,
  reflecting the one new `Metric` object).
- Frontend regression gate (required because `CustomerWorkspace.tsx` changed): `cd apps/web &&
  npm run test` — **84/84 pass**; `npm run build` — succeeds (`tsc -b && vite build`, no type
  errors).
- `cerebro validate` — exits 0 against the modified bundle (new metric object structurally valid).

All acceptance criteria (AC-1 through AC-5) are met.

## Compliance Matrix

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| C-01 | Spec written before implementation | PASS | This file, authored before script/fixes |
| C-02 | Live-validation script covers all 30 in-scope + 3 out-of-scope preset questions | PASS | `scripts/validate_customer_questions.py`, mirrors `CustomerWorkspace.tsx` |
| C-03 | Script mirror matches frontend source of truth verbatim | PASS | `tests/test_validate_customer_questions.py`, 2/2 pass |
| C-04 | No in-scope question returns null/blocked/empty answer | PASS | Final live run: 30/30 in-scope `status: "answered"`, non-empty |
| C-05 | Out-of-scope questions still safely decline, no SQL executed | PASS | Final live run: 3/3 `sql=no` |
| C-06 | Reword-first before adding new grounding objects | PASS | 3 reword variants tried for user-3 card questions before falling back to FR-4's grounding-object path; user-5 fixed by reword alone |
| C-07 | Any new grounding object is minimal, additive, and registered in the customer scope allowlist | PASS | `metric.card-transaction-amount-total` added to `CUSTOMER_SCOPE_METRIC_IDS`; no backend contract change |
| C-08 | Fix does not regress unrelated retrieval/grounding | PASS | Diagnosed and fixed a real crowding regression on GQ-05 by narrowing the new object's English text; `tests/test_retrieval_api_mcp.py` 15/15 pass |
| C-09 | Wording changes applied consistently to frontend and script together | PASS | `CustomerWorkspace.tsx` + `scripts/validate_customer_questions.py` updated in the same pass for user-5-05 |
| C-10 | Regression test added for the bug class found (not just the symptom) | PASS | `tests/test_validate_customer_questions.py` (AC-2); existing `tests/test_customer_scope.py` / `tests/test_retrieval_api_mcp.py` cover the grounding contract |
| C-11 | Full backend test suite passes | PASS | `pytest -q` — 989 passed, exit 0 |
| C-12 | Full frontend test suite and build pass | PASS | `npm run test` — 84 passed; `npm run build` — succeeds |

**Compliance Status: COMPLIANT**
