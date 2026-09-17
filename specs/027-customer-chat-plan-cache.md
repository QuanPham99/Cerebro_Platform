# 027 — Trim and cache the customer self-service chat query-plan step

## Problem

A customer self-service chat turn (`ChatOrchestrator.chat`, `src/cerebro/chat.py`,
`request.customer_id` set) goes through the same pipeline as an internal analyst turn,
but two things make it slower than it needs to be:

1. Every turn — customer or internal — builds `_exploration_inventory` (a full JSON
   dump of every live DuckDB table schema plus every active bundle object's semantic
   contract, `chat.py:851-892`) and embeds it in the `query_plan` and `semantic_answer`
   prompts as `available_metadata` (`chat.py:676-685`). This exists to power the
   internal "what tables/objects exist" metadata-exploration shortcut
   (`_is_metadata_exploration_question`, `chat.py:616`), which is already unconditionally
   disabled for customer requests. For a customer turn this data is built and shipped
   into two prompts for a feature that never runs, inflating prompt size and latency
   with no behavioral benefit, and contradicting the platform's design intent that
   customers only ever see the customer-centric object set (the same set already
   enforced by `grounding(..., allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS)` at
   `chat.py:656`).
2. The customer workspace (`apps/web/src/CustomerWorkspace.tsx`) is a closed system: 5
   simulated customers each choose from a small, fixed set of preset questions (plus a
   shared out-of-scope set), and the row-level security filter is applied
   deterministically *after* the model responds, by `SQLGuardrail`
   (`customer_scope.CUSTOMER_ROW_FILTER_TABLES`), never by the model itself. So the
   merged `QueryPlanAndSQL` LLM call's output for a given question text is expected to
   be identical regardless of which customer asks it or how many times it's asked — yet
   today it re-runs from scratch, at full LLM latency, on every single turn, including
   exact repeats.

## Goal

For a customer-scoped chat turn (`request.customer_id is not None`):

- The `query_plan`/`semantic_answer` prompts no longer include `available_metadata`.
- A repeated first-turn question (empty `request.history`, same canonicalized question
  text as a prior turn served by the same `ChatOrchestrator` instance) skips the
  `query_plan` LLM call entirely and reuses the previously generated plan+SQL, while
  still running full guardrail validation, execution, and answer synthesis against that
  customer's own data.

The internal/analyst chat path (`request.customer_id is None`) is unaffected in both
behavior and prompt content.

## Non-Goals

- Any change to the internal/analyst chat path, `_exploration_inventory` itself, or the
  `_is_metadata_exploration_question` shortcut's behavior when it does run.
- Any change to `SQLGuardrail.validate`, the row-level filter, `sql_repair`, execution,
  or `database_answer` — these run unconditionally and identically whether or not the
  plan came from cache.
- A general-purpose or persistent cache. This is a small, in-memory, per-orchestrator-
  instance cache scoped specifically to the customer workspace's closed-question
  property; it MUST NOT be extended to the open-ended internal chat path.
- Caching across bundle versions, server restarts, or multiple orchestrator instances.
- Skipping or caching the embedding/grounding step (`SemanticRetriever.grounding`) —
  deferred; see "Future work" below.

## Functional Requirements

- FR-1: in `ChatOrchestrator.chat`, the block that builds and attaches
  `context["available_metadata"]` (`chat.py:676-685`) MUST run only when
  `request.customer_id is None`. For a customer-scoped request, `context` MUST NOT
  contain an `available_metadata` key, and `self._exploration_inventory` /
  `self.executor.table_schemas` MUST NOT be called for that turn.
- FR-2: `ChatOrchestrator.__init__` MUST create an empty, instance-scoped cache (e.g.
  `self._plan_cache: dict[tuple[str, str], QueryPlanAndSQL] = {}`) with no persistence
  beyond the orchestrator instance's lifetime.
- FR-3: a chat turn is cache-eligible only when `request.customer_id is not None` AND
  `request.history` is empty. Any turn with non-empty history — for either a customer
  or internal request — MUST always call `query_plan` fresh and MUST NOT read from or
  write to the cache.
- FR-4: the cache key MUST be `(self.bundle.version, canonicalize_question(request.message))`,
  using `canonicalize_question` from `src/cerebro/provenance.py` unchanged. The key MUST
  NOT include `customer_id`, so the shared out-of-scope questions (and any other
  question text asked by more than one simulated customer) can hit across customers.
- FR-5: on a cache hit, `ChatOrchestrator.chat` MUST use a `model_copy()` of the cached
  `QueryPlanAndSQL` as `plan`, MUST NOT call `self._generate("query_plan", ...)`, and
  MUST otherwise continue the existing turn logic unchanged (clarification/no-query
  short-circuits, `SQLGuardrail.validate`, `sql_repair`, execution, `database_answer`,
  trace construction) exactly as it does for a freshly generated plan.
- FR-6: on a cache miss for a cache-eligible turn, `ChatOrchestrator.chat` MUST call
  `self._generate("query_plan", ...)` as it does today and then store a `model_copy()`
  of the resulting plan in `self._plan_cache` under the FR-4 key, before continuing.
- FR-7: a cache hit MUST be logged with the same structured-logging convention used for
  `chat.llm.started`/`chat.llm.completed` (e.g. `chat.plan_cache.hit request_id=...
  cache_key=...`), so hits are observable in server logs.
- FR-8: `SQLGuardrail.validate(..., customer_id=...)` MUST run unconditionally on every
  turn, including cache hits — this requirement exists to make explicit that the cache
  never bypasses row-level security enforcement.

## Acceptance Criteria

- AC-1: for a non-customer request, the mock provider in `tests/test_chat.py` continues
  to record `available_metadata` in the `query_plan` prompt, unchanged from today
  (`test_chat_runs_validated_read_only_query` passes with no assertion changes).
- AC-2: for a customer-scoped request (`customer_id` set), the captured `query_plan`
  prompt does NOT contain `"available_metadata"`, and a mock/spy on
  `ChatOrchestrator._exploration_inventory` (or `DuckDBQueryExecutor.table_schemas`)
  records zero calls for that turn.
- AC-3: two customer-scoped requests with identical `message` and empty `history` (same
  or different `customer_id`) result in exactly one `query_plan` provider call across
  both turns; `database_answer` is still called once per turn (its content differs
  because query results differ).
- AC-4: two customer-scoped requests with identical `message` but non-empty `history`
  result in two separate `query_plan` provider calls (no caching).
- AC-5: two internal (`customer_id=None`) requests with identical `message` and empty
  history result in two separate `query_plan` provider calls (cache never applies to
  the internal path).
- AC-6: a freshly constructed `ChatOrchestrator` has an empty `_plan_cache`; a plan
  cached by one orchestrator instance is not visible to a second, separately
  constructed instance.
- AC-7: on a forced cache hit, the response still reflects `SQLGuardrail`'s row-level
  filter for the requesting customer (rerun the existing customer-scope SQL-filter
  assertion from `tests/test_customer_scope.py` after priming the cache with one prior
  call).
- AC-8: `pytest tests/test_chat.py tests/test_customer_scope.py` and the full `pytest`
  suite pass.

## Test Design

Extend `tests/test_chat.py`'s existing `ChatProvider`-recording fixture pattern (used by
spec 025's tests) to count `generate` calls per schema name across multiple `chat()`
invocations against the same `ChatOrchestrator` instance. Add:

- A customer-path prompt-content test (AC-2), spying on `_exploration_inventory`/
  `table_schemas` call count.
- A same-orchestrator, two-call repeat-question test for the customer path (AC-3) and
  for the non-empty-history and internal-path variants (AC-4, AC-5).
- A two-orchestrator-instances test (AC-6).
- A cache-hit-still-enforces-row-filter test (AC-7), likely alongside the existing
  Phase B tests in `tests/test_customer_scope.py`.

No new test infrastructure beyond what `test_chat.py`/`test_customer_scope.py` already
have (`bank_database`, `_settings`, `ChatProvider`) is needed.

## Future work (explicitly deferred, not part of this spec)

On a plan-cache hit for a query-requiring question, `SemanticRetriever.grounding`'s
embedding call is computed but its output (`context`) is otherwise unused, since the
merged `query_plan` prompt that would have consumed it is skipped — only
`evidence_ids` (from the cheap lexical/graph ranking) is actually needed downstream. A
future change could skip the embedding step specifically on a plan-cache hit. This is
not implemented here to keep this spec's surface to the two changes above.
