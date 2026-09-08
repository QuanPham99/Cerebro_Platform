# 013 — Generation Observability UI

## Goal

Let a data owner run Cerebro's existing catalog-to-OKF candidate workflow from the Semantic Constellation and follow every real stage without implying that Cerebro creates the physical database schema or silently activates AI-proposed semantics.

## Functional Requirements

- FR-801: Reuse one generation workflow from CLI and HTTP entry points; the web server must not spawn the CLI as a subprocess.
- FR-802: Emit ordered, replayable progress for source checks, catalog discovery, two structural semantic-agent stages, the compatible skipped query-semantics stage, semantic linking/OKF compilation, validation, and candidate readiness.
- FR-803: Keep catalog discovery read-only and row-free, and never expose credentials, database paths, prompts, source rows, or raw provider responses in browser events.
- FR-804: Allow at most one unfinished generation run per server process and write each run to a unique candidate directory.
- FR-805: Expose validated candidate graph and object inspection without changing the active-bundle pointer.
- FR-806: Show the live workflow as a guided transcript plus a synchronized stage ribbon inside Semantic Constellation.
- FR-807: Clearly distinguish active and candidate graph modes and provide a direct return to the active bundle.
- FR-808: Preserve credential-free structural fallback generation and identify skipped model stages in the transcript.
- FR-809: Use a bounded, environment-configurable model timeout suitable for full catalog generation stages.
- FR-810: Bound generation output size and attempt one schema-guided repair when the compatible provider returns malformed JSON.
- FR-811: Keep external stage IDs stable while presenting them as `business_semantics` = Semantic inventory, `relationship_semantics` = Relationship semantics, `query_semantics` = Metrics and rules after activation, and `compile_okf` = Link and compile OKF.
- FR-812: Every started/completed/skipped semantic-stage event includes a sanitized `details.agent_id`: `semantic_inventory`, `relationship`, or `metric_rule`. No browser event exposes prompts or provider responses.
- FR-813: The Semantic generation tab restores its current run from a browser-session run ID, replays server state after refresh, and reconnects SSE only for queued or running runs. Unknown run IDs are discarded without blocking a new run.
- FR-814: Before and during generation, the Semantic generation tab does not render the active graph, graph navigation, graph legend, or graph controls. After successful validation it may render only that run's candidate graph.
- FR-815: Expose an ordered run trace whose agent steps contain the exact sanitized structured inputs passed into each typed agent and the validated typed outputs returned by it. Never expose database paths, source rows, credentials, prompt wrappers, raw provider responses, stack traces, or checked-in oracle semantics.
- FR-816: The Build smoke-test action always starts `database_only` generation from raw DuckDB metadata. It exposes no configured-source selector; the existing API and CLI defaults remain backward compatible.
- FR-817: Keep review and activation as a separate human gate after the automated pipeline. Completing the smoke test never approves or activates the candidate.
- FR-818: Keep the active bundle and Semantic generation tabs visually ordered as left-aligned names with right-aligned version or run status.
- FR-819: The trace marks metric/rule generation as intentionally skipped for post-activation authoring, and compile output reports those categories as empty without changing stable stage IDs or exposing raw prompts or provider responses.
- FR-820: Keep `bank-workshop` v0.2.0 available as the permanent golden graph in the first tab. The second tab owns the retained smoke run, candidate, activated generated graph, and authored definition revision state.
- FR-821: Before a smoke candidate exists, the second tab renders the Build workbench without any graph, navigator, legend, or graph controls. Switching tabs must not reset the current run.

## Acceptance Criteria

- `cerebro generate` prints human-readable stage progress to stderr and retains its final JSON result on stdout.
- Starting a web run returns immediately; its event stream can replay events and terminates after success or failure.
- A second start while a run is active receives a typed conflict that identifies the existing run.
- A successful run produces a separately validated candidate whose graph and objects are available to the UI, while the active bundle remains unchanged.
- Failed runs return a sanitized stage error and never expose a candidate preview.
- Semantic Constellation can start a run, narrate every stage, preview the candidate, inspect its objects, and return to the active graph.
- Switching semantic versions or workspaces and refreshing the page within the same browser session preserves the current run while the API process remains alive.
- The generation workbench shows sanitized per-stage input/output and never falls back to the live graph while a candidate is unfinished.
- The generation experience is a fixed guided action, not a free-form model prompt and not an activation or editing surface.
- Automated tests use mocked or credential-free providers and never call a developer's live endpoint.
- Existing API routes, event stage IDs, review records, and explicit activation contracts remain backward compatible.

## Boundaries

Runs and event history are in-memory and local to one server process; completed candidate artifacts remain on disk. Specification 010 extends this workflow with database-only discovery, whole-candidate review, and explicit activation. Durable jobs, authentication, candidate editing, schema DDL, and remote orchestration remain out of scope.

Source-row relationship profiling and the future Text-to-SQL semantic/physical planner decomposition remain out of scope.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-801 | FR-802, FR-811 | Assert the stable event sequence and presentation labels through CLI and web mappings. |
| T-802 | FR-803, FR-812 | Assert all semantic events carry the correct `agent_id` and never contain paths, rows, prompts, secrets, or raw responses. |
| T-803 | FR-804–FR-810 | Exercise replay, concurrency conflict, fallback, candidate preview, timeout, repair, and sanitized failure behavior. |
| T-804 | FR-813, FR-814 | Restore a running/completed run from browser session state, reconnect only unfinished SSE, and assert that no graph UI renders until the candidate succeeds. |
| T-805 | FR-815 | Assert ordered trace state, typed upstream agent inputs, validated outputs, and recursive absence of paths, rows, prompts, secrets, raw responses, and oracle semantics. |
| T-806 | FR-816–FR-818 | Assert fixed raw-database smoke mode, separate governance gate, and left-name/right-status tab layout. |
| T-807 | FR-819 | Assert the skipped metric/rule stage makes no provider call and compilation reports zero deferred categories. |
| T-808 | FR-820, FR-821 | Assert golden v0.2.0 remains selectable, second-tab state survives tab switches/refresh, and no graph UI renders before candidate readiness. |
