# 031 — Reduce chat-answer latency via per-call reasoning control and a plan-cache grounding skip

## Problem

A query-requiring chat turn (`ChatOrchestrator.chat`, `src/cerebro/chat.py`) makes two
sequential LLM round-trips through `OpenAICompatibleGateway` (`src/cerebro/llm.py`), the
gateway that actually serves live chat (not `hosted_provider.py`'s `OrganizerModelGateway`,
which is a separate transport used only by the guarded Text2SQLAgent/`cerebro baseline`
path and is unaffected by this spec):

1. `query_plan` (chat.py, spec 025's merged plan+SQL call) — genuinely needs the
   configured model's reasoning to pick correct grounding and produce correct SQL.
2. `database_answer` (chat.py) — takes the *already-executed* query result (`columns`,
   `rows`, `truncated`) plus the original question and produces exactly one sentence
   (`AnswerPayload.answer: str`, `src/cerebro/models.py`). By this point every decision
   (grounding, SQL, execution) has already happened; this call only narrates known rows,
   guarded against treating row values as instructions and against inventing missing
   values.

Both calls run through `OpenAICompatibleGateway.generate()` with identical settings —
nothing today lets a caller ask for less reasoning depth or a tighter output-token bound
on a per-call basis, even though `database_answer` needs neither. The configured model
(`z-ai/glm-5.2-hackathon`, GreenNode/VNG Cloud gateway, `CEREBRO_LLM_PROVIDER_ID=
greennode-glm`) is confirmed (specs/018, current `evaluation/customer-validation-runs/
*.jsonl` recordings) to be reasoning-bound: most of a turn's wall-clock time is spent in
the model's own internal reasoning pass, not in retrieval, network transport, or SQL
execution. GLM-5.2's OpenAI-compatible API accepts a non-standard `thinking` field
(`{"type": "enabled"|"disabled"}`) to toggle that internal reasoning pass per call.
Disabling it for `database_answer` removes a full reasoning pass from one of the two
calls in the pipeline without touching the model, provider, or `query_plan`'s behavior.

Separately, a customer-workspace plan-cache hit (`_plan_cache`, spec 027) still pays for
a full `SemanticRetriever.grounding()` call, including its per-request embedding network
call, even when the cached plan's branch never consumes the resulting grounding context
— an already-documented gap in spec 027's "Future work".

## Goal

Without changing the configured model or provider:

- The `database_answer` call runs with the model's internal reasoning pass disabled and
  a tighter output-token cap than the default, while producing behaviorally the same
  kind of single-sentence answer.
- `query_plan` and `sql_repair` are unaffected: identical reasoning depth, identical
  request-body shape to today.
- A plan-cache hit whose cached plan will not need full grounding context downstream
  (i.e. it will execute a query or return a clarification, not fall into the no-query
  `semantic_answer` branch) skips the embedding call entirely.

## Non-Goals

- `hosted_provider.py` / `OrganizerModelGateway` / the guarded Text2SQLAgent /
  `cerebro baseline` path — a separate transport, not touched here. A future spec may
  apply the same treatment there.
- `semantic_answer` (chat.py, the no-query narration call) — structurally similar to
  `database_answer` (single-sentence `AnswerPayload`, no decision-making) but not
  included in this change; left for a future spec.
- Streaming responses — this reduces total latency, not time-to-first-token.
- Any change to `SQLGuardrail`, execution, the row-level filter, or `sql_repair`'s retry
  behavior.
- Lowering `query_plan`/`sql_repair`'s reasoning effort — explicitly out of scope to
  avoid an SQL-correctness regression.
- A general-purpose cache extension — spec 027's cache-eligibility rules (customer-scoped,
  empty history) are unchanged; this spec only changes what a hit does with grounding.

## Functional Requirements

- FR-1: `Settings` (`src/cerebro/settings.py`) MUST gain a computed property
  `llm_supports_reasoning_control: bool`, `True` if and only if
  `self.llm_provider_id == "greennode-glm"`. This is the only gate for ever sending the
  non-standard `thinking` field, so a deployment using a different/unknown
  OpenAI-compatible provider (e.g. the default `"openai-compatible"`) is never sent a
  body field it might reject.
- FR-2: the `GenerationProvider` ABC (`src/cerebro/semantic/agents.py`) and
  `OpenAICompatibleGateway.generate()` (`src/cerebro/llm.py`) MUST gain two new
  keyword-only parameters: `thinking: bool = True` and
  `max_output_tokens: int | None = None`. Every existing call site that omits them MUST
  see byte-identical behavior to today.
- FR-3: when `OpenAICompatibleGateway.generate()` (or its internal `_repair_json`
  fallback) is called with `thinking=False` and `settings.llm_supports_reasoning_control`
  is `True`, the request to `chat.completions.create` MUST include
  `extra_body={"thinking": {"type": "disabled"}}`. In every other case (`thinking=True`,
  or `thinking=False` with `llm_supports_reasoning_control` `False`), no `extra_body` key
  for `thinking` MUST be sent.
- FR-4: the `max_tokens` sent to the provider MUST be `max_output_tokens` when the caller
  supplies a non-`None` value, else `settings.llm_max_output_tokens` (today's unconditional
  behavior, unchanged when the parameter is omitted).
- FR-5: `ChatOrchestrator._generate()` (`src/cerebro/chat.py`) MUST gain optional
  keyword-only `thinking: bool | None = None` and `max_output_tokens: int | None = None`,
  forwarded to `self.provider.generate(...)` only when not `None`. Every existing call
  site (`query_plan`, `sql_repair`, `semantic_answer`) MUST continue to omit both,
  producing an identical call to `self.provider.generate(schema_name, prompt,
  output_model)` as today.
- FR-6: the `database_answer` call site in `ChatOrchestrator.chat()` MUST pass
  `thinking=False` and `max_output_tokens=_DATABASE_ANSWER_MAX_OUTPUT_TOKENS` (a new
  module-level constant in `chat.py`, value `512`).
- FR-7: `SemanticRetriever.search()` and `.grounding()` (`src/cerebro/retrieval.py`) MUST
  gain a keyword-only `skip_vector: bool = False`. When `True`, `search()` MUST NOT call
  `_vector_rank()` (and therefore MUST NOT invoke the embedder), fusing lexical and graph
  signals only. Every existing caller that omits it MUST see identical behavior to today.
- FR-8: in `ChatOrchestrator.chat()`, the cache-key lookup and `cached_plan` retrieval
  (today computed after `grounding()` runs) MUST be moved to before the call to
  `self.retriever.grounding(...)`. `grounding(...)` MUST be called with
  `skip_vector=True` if and only if there is a cache hit (`cached_plan is not None`) AND
  the cached plan will not take the no-query `semantic_answer` branch (i.e. NOT
  (`not cached_plan.clarification and not cached_plan.requires_query`)). A cache miss
  MUST always call `grounding(...)` with `skip_vector=False` (today's behavior,
  unchanged).
- FR-9: on a `skip_vector=True` cache hit, `evidence_ids`/`warnings` returned to the
  caller are computed from lexical+graph ranking only and MAY differ in composition or
  order from a fresh (cache-miss) grounding call for the identical question. This is an
  accepted trade-off: the cached plan's SQL and `SQLGuardrail`'s row-level/table/column
  enforcement never depend on vector ranking.

## Acceptance Criteria

- AC-1: a captured request (via a fake `chat.completions` double in `tests/test_llm.py`)
  for a `thinking=False` call with `llm_provider_id="greennode-glm"` includes
  `extra_body == {"thinking": {"type": "disabled"}}`.
- AC-2: a captured request for a `thinking=True` (or default) call includes no
  `extra_body` key at all, regardless of `llm_provider_id`.
- AC-3: a captured request for a `thinking=False` call with `llm_provider_id` set to the
  default `"openai-compatible"` includes no `extra_body` key.
- AC-4: a captured request's `max_tokens` equals a supplied `max_output_tokens` override
  when given, else `settings.llm_max_output_tokens`.
- AC-5: in `tests/test_chat.py`, the recorded `database_answer` call carries
  `thinking=False` and `max_output_tokens=512`; the recorded `query_plan` and
  `sql_repair` calls carry neither kwarg (i.e. are called exactly as today, with 3
  positional arguments and no `thinking`/`max_output_tokens`).
- AC-6: two customer-scoped, empty-history, identical-question turns where the cached
  plan has `requires_query=True` (a normal query-executing question) result in exactly
  one embedder call total across both turns (the cache-miss first turn); the plan-cache
  hit on the second turn makes zero additional embedder calls.
- AC-7: two customer-scoped, empty-history, identical-question turns where the cached
  plan has `requires_query=False` (an out-of-scope decline) still make a full grounding
  call — including an embedder call — on the cache-hit second turn, and the
  `semantic_answer` prompt still contains a non-empty `grounding` context.
- AC-8: `pytest tests/test_llm.py tests/test_chat.py tests/test_customer_scope.py` and
  the full `pytest` suite pass.

## Test Design

- `tests/test_llm.py`: extend the fake `chat.completions.create` double (already records
  call kwargs) with cases for AC-1 through AC-4: default call (no `extra_body`),
  `thinking=False` + `greennode-glm` (has `extra_body`), `thinking=False` +
  `openai-compatible` (no `extra_body`), and `max_output_tokens` override vs. default.
- `tests/test_chat.py`: update the test-double provider classes whose `generate()` is
  actually invoked for the `database_answer` stage (`ChatProvider`, `RepairingProvider`)
  to accept and record `thinking`/`max_output_tokens` kwargs; add an assertion per AC-5.
  `CancellingProvider`, `ClarifyingProvider`, `SemanticOnlyProvider` short-circuit before
  `database_answer` and need no signature change — verify by rerunning their tests
  unmodified.
- `tests/test_customer_scope.py`: update `_CountingProvider`/`_StubProvider` the same
  way; inject a call-counting fake embedder into the `SemanticRetriever` used by the
  plan-cache tests and add cases for AC-6 (query-requiring cache hit, zero extra embedder
  calls) and AC-7 (decline cache hit, embedder still called, `semantic_answer` still has
  grounding context).
- Run the full suite once after all edits; any test double whose `generate()` signature
  this spec missed surfaces immediately as `TypeError: unexpected keyword argument`
  rather than a silent behavior change.

## Future work (explicitly deferred, not part of this spec)

- Apply `thinking=False` to `semantic_answer` (structurally identical to
  `database_answer`), pending explicit confirmation.
- Apply equivalent `thinking`/`max_tokens` wiring to `hosted_provider.py`'s
  `OrganizerModelGateway` for the Text2SQLAgent/`cerebro baseline` path.
- Evaluate a lower `reasoning_effort` (as distinct from disabling `thinking` entirely)
  for `query_plan`/`sql_repair`, measured against the golden question set before any
  change.
