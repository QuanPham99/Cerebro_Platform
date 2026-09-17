# 025 — Combine the chat query-plan and SQL-proposal LLM calls

## Problem

A single governed chat turn that requires a database query (`ChatOrchestrator.chat`,
`src/cerebro/chat.py`) makes three sequential LLM round-trips: `query_plan`, then
`sql_proposal` (plus up to one `sql_repair` retry on guardrail rejection), then
`database_answer`. Each is a full network+model latency hop. `query_plan` and
`sql_proposal` are always both called for any query-requiring turn, from the same
grounding context produced in the same turn — the plan step never changes what
grounding the SQL step sees, and nothing downstream of `query_plan` conditionally
decides not to ask for SQL other than the two short-circuits the code already checks
(`plan.clarification`, `not plan.requires_query`). Removing this redundant round-trip
cuts the query path from 3 sequential model calls to 2 without touching retrieval,
guardrail validation, execution, or answer synthesis.

## Goal

For a chat turn where the model determines `requires_query=true` and no clarification
is needed, the orchestrator makes exactly one LLM call that produces both the plan
fields and the SQL proposal, replacing today's two sequential calls. The clarification
path and the no-query `semantic_answer` path are unaffected. `database_answer` remains
a separate call made after execution, since it depends on query results that don't
exist yet at plan time.

## Non-Goals

- Removing or restructuring the `sql_repair` retry loop (`chat.py:652-671`) — it only
  fires on guardrail validation failure and is unrelated to this change.
- Changing `database_answer`, retrieval/grounding, or the customer-scope row filter.
- Any change to `Text2SQLAgent` (spec 008) — this is the plain `ChatOrchestrator` path.

## Functional Requirements

- FR-1: `src/cerebro/models.py` MUST gain a combined response schema (e.g.
  `QueryPlanAndSQL`) carrying every field `QueryPlan` has today (`intent`,
  `requires_query`, `tables`, `metrics`, `filters`, `group_by`, `clarification`) plus
  `sql: str | None` and `explanation: str = ""`, used only for this merged call.
  `QueryPlan` and `SQLProposal` MUST remain unchanged and MUST still be used by
  `sql_repair`, which stays a single-purpose repair call returning `SQLProposal`.
- FR-2: `ChatOrchestrator.chat` MUST replace the `query_plan` call followed by a
  conditional `sql_proposal` call (`chat.py:594-649`) with one call using the combined
  schema and a merged prompt covering both the planning instructions and the SQL
  generation rules (approved tables/columns/joins, no `SELECT *`, no DDL/DML, etc.).
- FR-3: the existing `clarification` short-circuit and `not requires_query`
  short-circuit MUST behave identically to today, reading the relevant fields off the
  single merged response instead of a first-stage response.
- FR-4: when `requires_query=true` and no clarification is present, the merged
  response's `sql` field MUST be treated exactly as today's `sql_proposal.sql` was —
  passed into `SQLGuardrail.validate`, subject to the same up-to-one `sql_repair` retry
  on failure.
- FR-5: the `AgentTrace` entries the orchestrator returns MUST still include a
  `query_planner` summary and a `sql_generation` summary for a query-requiring turn
  (both derived from the one merged response), so `apps/web/src/App.tsx`'s trace
  rendering is unaffected.
- FR-6: request cancellation (`ChatCancellation`) MUST still be able to interrupt the
  turn at the same logical points — before the merged call, and before/after each
  subsequent step — matching today's checkpoint placement.

## Acceptance Criteria

- AC-1: for a query-requiring question with no guardrail rejection, the mock provider
  in `tests/test_chat.py` records exactly 2 `generate` calls for the turn (merged
  `query_plan`, then `database_answer`), down from today's 3.
- AC-2: `test_chat_runs_validated_read_only_query` continues to pass with an updated
  call-count assertion; its existing SQL/answer/row assertions are unchanged.
- AC-3: `test_chat_cancellation_stops_after_an_in_flight_provider_call` continues to
  pass unmodified — cancellation after the first (now only) call still raises
  `ChatCancelled` before any further model call happens.
- AC-4: a new test asserts a guardrail-rejected SQL proposal still triggers exactly one
  `sql_repair` call, and that a clarification response and a no-query semantic-answer
  response are produced from a single merged call with no `sql_proposal`-shaped second
  call.
- AC-5: `pytest tests/test_chat.py` and the full `pytest` suite pass.

## Test Design

Extend `tests/test_chat.py`'s `ChatProvider` fixture (or add a variant) to return the
combined schema for the merged stage name, and add/update assertions per AC-1 through
AC-4. No new test infrastructure or fixtures beyond what `test_chat.py` already has
(`bank_database`, `_settings`, `ChatProvider`) are needed.
