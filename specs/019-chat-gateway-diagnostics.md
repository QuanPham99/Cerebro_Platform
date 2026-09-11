# 019 — Chat gateway diagnostics and correlated console logging

## Problem

On 2026-09-11, the deployed GreenNode Agent Runtime returned Kong `502` for the basic preset
`Có bao nhiêu khách hàng theo từng giới tính?` after 54.35 seconds. The same deployment reported
`200` from `/health` immediately afterward, and `/api/runtime/status` advertised a 120-second LLM
timeout with two retries. The application therefore remained healthy while the managed gateway
ended a long-running `/api/chat` request before Cerebro or its configured model could answer.

The frontend discarded Kong's JSON `message` and `request_id`, reducing this actionable failure to
`Semantic API returned 502`. The server also lacked request-correlated stage timing, so its console
could not show whether the cutoff occurred during planning, SQL generation, execution, or answer
synthesis.

## Goal

- Preserve the Semantic API's upstream message and request ID in browser-visible errors.
- Correlate each user message, LLM stage, and final chat response by the client-generated request ID.
- Cancel backend work after a frontend-observed 5xx or network cutoff so abandoned model pipelines
  do not continue through later stages.
- Keep raw database result rows out of persistent server logs; the browser may log the complete
  response already delivered to that user.

## Functional Requirements

- FR-1: `apps/web/src/api.ts` MUST parse an error response once and retain FastAPI `detail`, Kong
  `message`, and a body/header request ID when present.
- FR-2: `postChat` MUST write structured browser-console events for the current user message,
  successful Semantic API response, and failed Semantic API response, including request ID and
  elapsed milliseconds.
- FR-3: after a network failure or HTTP 5xx, the chat workspace MUST request cancellation for the
  same request ID. A cancellation failure MUST be logged but MUST NOT replace the original error.
- FR-4: the server MUST log request receipt, worker lifetime, each model stage's start/completion/
  failure, final response, cancellation, and unhandled failure with one request ID and elapsed time.
- FR-5: the server's final-response log MUST include status, answer, SQL, columns, row count,
  warnings, and trace, but MUST omit raw `rows` and record only `rows_omitted`.

## Acceptance Criteria

- AC-1: a Kong-style `{message, request_id}` `502` becomes an error containing the status, message,
  and request ID and produces a structured browser-console error event.
- AC-2: a successful chat produces paired browser-console user/response events with the same
  request ID and logs every model stage in the server console.
- AC-3: server logging tests prove the current user message and response summary are present while
  a raw row value is absent.
- AC-4: focused frontend and backend chat tests pass, followed by the full frontend build and Python
  suite.

## Deployment Boundary

Logging does not raise GreenNode's gateway timeout. The operator must increase the managed request
timeout above the observed model latency (some preset validation runs exceeded 200 seconds), select
a faster model, or adopt a streaming/asynchronous chat transport in a separate compatibility change.
