# 032 — Recover from a truncated `database_answer` reply instead of failing the turn

## Problem

Spec 031 capped the `database_answer` narration call at 512 output tokens
(`_DATABASE_ANSWER_MAX_OUTPUT_TOKENS`, `src/cerebro/chat.py`) to cut latency, on the
premise that the call "produces exactly one sentence". That premise does not hold when the
executed query returns a wide result set: the configured model (`z-ai/glm-5.2-hackathon`)
narrates the rows one by one and runs past the cap.

When the cap is reached, the provider returns `finish_reason == "length"` and a JSON string
that stops mid-token. `OpenAICompatibleGateway.generate()` (`src/cerebro/llm.py`) then fails
the whole turn:

- the truncated content raises `json.JSONDecodeError` inside `_decode_json_content`;
- the failure happens on the *first* configured response mode (`json_schema` under the
  default `auto`), so the `index + 1 == len(modes)` guard is false and neither the
  `json_object` fallback nor `_repair_json` is reached;
- `GenerationOutputError("database_answer")` propagates, surfacing as
  `Model output did not match the required database_answer schema`.

Measured against the live GreenNode gateway on 2026-09-20 with a 100-row, 3-column result
(the row limit a governed query is allowed to return), 6 of 6 calls at a 512-token cap
returned `finish_reason=length` with unparseable JSON — 3 with reasoning disabled
(512/512 completion tokens, 1326–1565 characters of partial content) and 3 with reasoning
enabled (660–993 completion tokens). A single wide-result question is therefore a coin
flip, and `ReportOrchestrator.run()` (spec 023) issues up to `MAX_SECTIONS = 7` such calls
in sequence, so an executive report on the deployed GreenNode Agent Runtime fails routinely
— observed as `REPORT AGENT · FAILED` in the web UI.

Recovery is impossible for the caller: the response is already spent, the partial content is
discarded, and no component above the gateway knows the reply was truncated rather than
malformed.

## Goal

Make a truncated reply recoverable inside the gateway, and make the narration prompt produce
answers that fit, so a governed answer over a wide result set completes instead of failing —
while keeping spec 031's latency gain for the common narrow-result turn.

## Non-goals

- Changing the model, provider, response-mode negotiation, or `query_plan`'s budget.
- Raising the default `_DATABASE_ANSWER_MAX_OUTPUT_TOKENS` for every call (that would give
  the latency back on every turn, including the narrow-result majority).
- Changing what rows the executor returns, the 100-row governed limit, or what
  `ChatResponse` carries.
- Retrying a reply that is malformed for any reason other than truncation — that path keeps
  spec 031's existing behavior (fallback mode, then `_repair_json`).
- Summarizing or trimming rows before they reach the model in a way that would let the
  narration omit material values (spec 023's "never invent a number" contract stands).

## Functional requirements

- FR-3201: When a provider reply reports `finish_reason == "length"`, the gateway MUST treat
  it as truncated rather than as malformed output, and MUST NOT surface the partial content
  to `_decode_json_content`.
- FR-3202: On a truncated reply the gateway MUST reissue the same request once, in the same
  response mode, with an output-token budget raised to
  `min(budget * _TRUNCATION_RETRY_MULTIPLIER, settings.llm_max_output_tokens)`.
- FR-3203: The retry MUST be attempted only when the effective budget is below
  `settings.llm_max_output_tokens`. A reply truncated at the configured ceiling MUST fail
  with `GenerationOutputError` as before, since reissuing it cannot succeed.
- FR-3204: A retry that itself comes back truncated MUST raise `GenerationOutputError` for
  that schema name rather than retrying further or emitting partial content.
- FR-3205: Truncation recovery MUST apply to every schema the gateway serves, not only
  `database_answer`, since any caller-supplied budget can be exceeded.
- FR-3206: A reply whose `finish_reason` is absent or any value other than `"length"` MUST
  follow the existing decode/validate path unchanged, so no behavior other than truncation
  recovery is altered.
- FR-3207: The `database_answer` prompt (`src/cerebro/chat.py`) MUST instruct the model to
  summarize the result set — stating the shape of the data and citing the values the
  question actually asks for — instead of enumerating every returned row, so the common case
  fits the spec 031 budget on the first call.
- FR-3208: Prompt wording MUST preserve the existing untrusted-data and
  do-not-invent-missing-values instructions verbatim in effect (spec 012, spec 023).

## Acceptance criteria

- AC-3201: A first reply carrying `finish_reason == "length"` and truncated JSON, followed
  by a complete reply, yields the validated object; the gateway issues exactly two provider
  calls, the second with a strictly larger `max_tokens` than the first, in the same response
  mode.
- AC-3202: A caller-supplied budget already equal to `llm_max_output_tokens` that returns
  truncated content raises `GenerationOutputError` after exactly one provider call.
- AC-3203: Two consecutive truncated replies raise `GenerationOutputError` naming the schema
  after exactly two provider calls.
- AC-3204: A complete reply with `finish_reason == "stop"`, and a reply from a provider that
  reports no `finish_reason` at all, each resolve in one call with no budget escalation.
- AC-3205: The retry budget never exceeds `llm_max_output_tokens`.
- AC-3206: The `database_answer` prompt contains a summarize-don't-enumerate instruction and
  still contains the untrusted-data and missing-values instructions.
- AC-3207: The existing spec 031 behavior is unchanged for an untruncated narrow-result
  turn: one call, `thinking` disabled where the provider supports it, budget 512.

## Edge cases

- EC-3201: A provider that omits `finish_reason` (or returns `None`) is indistinguishable
  from a completed reply; such a reply takes the unchanged path, and genuinely malformed
  content still reaches `_repair_json` through the existing fallback chain.
- EC-3202: A truncated reply in `json_schema` mode escalates the budget within that mode
  first; the `json_object` fallback remains reserved for response-format rejections.
- EC-3203: `llm_max_output_tokens` configured below the caller's budget yields no escalation
  headroom; FR-3203 makes this one call and one typed failure, not a silent retry loop.
- EC-3204: Truncation costs one extra round-trip, so a wide-result turn is slower than a
  narrow one. That is the intended trade: spec 031's budget still governs the common case.
- EC-3205: An empty reply (`content` falsy) keeps raising `GenerationOutputError`
  immediately; it is not truncation and a retry is not justified without evidence.

## Test design

| Test ID | Requirement / criterion | Level | Scenario | Expected result |
|---|---|---|---|---|
| T-3201 | FR-3201/3202, AC-3201 | Unit | Truncated reply, then a complete one | Validated output; two calls; second `max_tokens` larger; same mode |
| T-3202 | FR-3203, AC-3202/3205 | Unit | Budget already at `llm_max_output_tokens`, truncated reply | `GenerationOutputError`; exactly one call |
| T-3203 | FR-3204, AC-3203 | Unit | Two consecutive truncated replies | `GenerationOutputError`; exactly two calls |
| T-3204 | FR-3206, AC-3204 | Unit | `finish_reason == "stop"`, and a provider with no `finish_reason` | One call each; no escalation |
| T-3205 | FR-3205 | Unit | Truncated reply for a non-`database_answer` schema | Same escalation applies |
| T-3206 | FR-3207/3208, AC-3206 | Unit | Inspect the captured `database_answer` prompt | Summarize instruction present; untrusted-data and missing-values instructions present |
| T-3207 | AC-3207 | Unit | Narrow-result chat turn | One `database_answer` call, `thinking=False`, `max_output_tokens=512` |
