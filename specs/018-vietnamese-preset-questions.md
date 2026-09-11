# Feature: Vietnamese preset questions with live-validated SQL guarantee

## Problem

The Text-to-SQL chat workspace's preset panel (`PRESET_QUESTION_LEVELS` in
`apps/web/src/App.tsx`) shipped 5 tiers of English questions that were never validated live
against the deployed `ChatOrchestrator` path. Two concrete defects followed from that:

1. **Untranslated content.** All 26 preset questions were English-only, with no Vietnamese
   equivalent, despite the target audience needing Vietnamese.
2. **Presets that don't return a query.** The last "Really Hard" preset,
   `'Show me our best customers.'`, is a deliberately undefined-metric anti-pattern
   (`README.md:575-579` documents this exact question as the "ambiguous metric" example) — it
   always resolves to `status: "clarification"`, never SQL. Several other presets
   (`'Explain the approved joins for transaction amount by branch.'`, `'Explain how customer net
   cash flow is computed...'`) are explanatory/metadata questions with the same outcome. None of
   the 26 original questions had ever been run against a live server and inspected for
   `status`/`sql` before being shipped, so these dead-end presets went unnoticed until users hit
   them in production and reported spurious "Semantic API returned 502" / "too ambiguous" errors.

Investigation also found that `"Semantic API returned 502"` (`apps/web/src/api.ts:1-17`) is
generic frontend fetch-error text, not something the Cerebro backend emits — a 502 means the
reverse proxy / GreenNode Agent Runtime gateway couldn't reach a healthy app process (commit
`6f0d410` already fixed one such port/health mismatch). Live validation of this spec's question
set found a second, independent contributor: several proven, correctly-grounded questions
(matching `evaluation/golden-questions.yaml` patterns) took 100-240+ seconds end-to-end against
the configured model (`z-ai/glm-5.2-hackathon`), which is squarely in gateway-timeout territory —
this matches the pre-existing, already-accepted latency risk noted in
`specs/015-chat-grounding-and-validator-precision-fixes.md`'s Non-Goals ("3 preset test questions
that still time out at 400s client-side even without request pileup").

Live validation also surfaced a real `SQLGuardrail` gap (`src/cerebro/chat.py`): a query that
references a CTE-computed column via its CTE alias in the outer `SELECT` list (not just
`ORDER BY`/`HAVING`, which spec 015 already covers) is rejected with `Column is not in the
approved catalog: <alias>`, even though the column is a legitimate CTE output, not a raw catalog
column. Reproduced with:
```sql
WITH npl AS (
  SELECT l.branch_id, 100.0 * SUM(...) / NULLIF(COUNT(*), 0) AS non_performing_loan_rate
  FROM loans AS l GROUP BY l.branch_id
)
SELECT b.branch_name, n.non_performing_loan_rate FROM branches AS b
LEFT JOIN npl AS n ON n.branch_id = b.branch_id
-- blocked: "Column is not in the approved catalog: n.non_performing_loan_rate"
```
Fixing `SQLGuardrail` itself is out of scope here (`src/cerebro/chat.py` had unrelated in-flight
changes from concurrent work during this investigation) — this spec instead documents the gap and
avoids question shapes that reliably trigger it, and flags the validator fix as follow-up work.

## Goal

- Every preset question is in Vietnamese.
- Every preset question in a "requires SQL" tier resolves to `status: "answered"` with non-null
  `sql` when run live against `ChatOrchestrator` via `/api/chat` — verified by an actual run, not
  inspection.
- The preset list and its live-validation harness share one source of truth, so they cannot drift.
- Validation results are persisted incrementally (one JSONL line per question, flushed
  immediately), so a run can be reviewed question-by-question while still in progress, not only
  after the full batch finishes.

## Non-Goals

- Fixing `SQLGuardrail`'s CTE-alias-in-outer-`SELECT` gap (documented above) — flagged as
  follow-up work, not fixed in this spec. The one preset that hit it (`nang-cao-01`'s original
  "monthly transaction growth" phrasing) was reworded to a shape that doesn't trigger it, rather
  than patching the validator.
- Reducing per-question model latency (100-240s observed for several proven-pattern questions).
  This is an existing, accepted characteristic of the configured hosted model
  (`specs/015-...md` Non-Goals already documents a matching symptom), not something a content-only
  change can fix. Flagged as a deployment/gateway-timeout follow-up.
- Changing anything in `src/cerebro/chat.py`, `api.py`, `models.py`, or `apps/web/src/api.ts` —
  those had unrelated concurrent changes in flight (a chat-cancellation feature) during this work
  and are untouched here.

## Functional Requirements

- FR-1: `apps/web/src/presetQuestions.json` MUST be the single source of truth for preset
  question content, in the shape
  `[{ level: string; requiresSql: boolean; questions: string[] }]`, consumed by both
  `apps/web/src/App.tsx` (import) and `scripts/validate_preset_questions.py` (read from disk).
- FR-2: Every `questions[]` entry MUST be Vietnamese text.
- FR-3: Every entry in a tier with `requiresSql: true` MUST name a specific metric (or
  metric+dimension, or metric+business-rule combination) that exists in the active
  `knowledge/bank-workshop` bundle, worded specifically enough that the agent does not need to
  ask a clarifying question to resolve it.
- FR-4: No preset question MUST use an undefined superlative (e.g. "best", "top" with no named
  measure) or reference a concept absent from the bundle.
- FR-5: `scripts/validate_preset_questions.py` MUST send each question to a running server's
  `POST /api/chat` (the same endpoint and request shape the deployed frontend uses) and append one
  JSON result line to its output file immediately after each response, flushing to disk before
  moving to the next question.
- FR-6: The harness MUST mark a `requiresSql: true` question as passing only when the response's
  `status == "answered"` and `sql` is non-null/non-empty; a `requiresSql: false` question passes
  regardless of `status` (metadata-only answers and clarifications are both acceptable for that
  tier).
- FR-7: The harness MUST support resuming a partial run (`--resume`), skipping ids that already
  have a passing record in the target output file, so an interrupted run (e.g. server restart)
  does not require re-running already-verified questions.

## Acceptance Criteria

- AC-1: `apps/web/src/presetQuestions.json` contains exactly 5 tiers (Giới thiệu, Cơ bản, Trung
  bình, Nâng cao, Khó) with 5-6 Vietnamese questions each, all originally-English content removed
  from `apps/web/src/App.tsx`.
- AC-2: A full live run of `scripts/validate_preset_questions.py` against a running
  `cerebro serve` instance, recorded in `evaluation/preset-validation-runs/run1.jsonl`, shows all
  20 `requiresSql: true` questions (Cơ bản/Trung bình/Nâng cao/Khó) with `status: "answered"` and
  non-null `sql`, and all 6 `Giới thiệu` questions with `passed: true` (metadata-exempt).
- AC-3: `npx vitest run` (`apps/web`) passes, including the updated `App.test.tsx` assertion that
  the first "Cơ bản" preset button (`'Có bao nhiêu khách hàng theo từng giới tính?'`) renders.
- AC-4: `npm run build` (`apps/web`) succeeds, confirming the `presetQuestions.json` import
  type-checks under `resolveJsonModule`.

## Edge Cases

- EC-1: A `requiresSql: false` question that happens to produce SQL anyway (observed for 2 of the
  6 Giới thiệu questions) is still counted as passing — FR-6 only imposes the stricter
  `status == "answered" && sql` bar on `requiresSql: true` tiers.
- EC-2: Non-deterministic generation means an identical question can occasionally time out on one
  attempt and pass comfortably on a retry with the same wording (observed for the final "Khó"
  question, 240s timeout then 202.6s pass on immediate retry with unchanged text) — the harness's
  `--resume` support exists specifically so a retry doesn't have to re-verify already-passing
  questions.
