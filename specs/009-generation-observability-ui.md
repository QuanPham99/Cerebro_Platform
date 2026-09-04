# 009 — Generation Observability UI

## Goal

Let a data owner run Cerebro's existing catalog-to-OKF candidate workflow from the Semantic Constellation and follow every real stage without implying that Cerebro creates the physical database schema or silently activates AI-proposed semantics.

## Functional Requirements

- FR-801: Reuse one generation workflow from CLI and HTTP entry points; the web server must not spawn the CLI as a subprocess.
- FR-802: Emit ordered, replayable progress for source checks, catalog discovery, three typed semantic stages, OKF compilation, validation, and candidate readiness.
- FR-803: Keep catalog discovery read-only and row-free, and never expose credentials, database paths, prompts, source rows, or raw provider responses in browser events.
- FR-804: Allow at most one unfinished generation run per server process and write each run to a unique candidate directory.
- FR-805: Expose validated candidate graph and object inspection without changing the active-bundle pointer.
- FR-806: Show the live workflow as a guided transcript plus a synchronized stage ribbon inside Semantic Constellation.
- FR-807: Clearly distinguish active and candidate graph modes and provide a direct return to the active bundle.
- FR-808: Preserve credential-free structural fallback generation and identify skipped model stages in the transcript.
- FR-809: Use a bounded, environment-configurable model timeout suitable for full catalog generation stages.
- FR-810: Bound generation output size and attempt one schema-guided repair when the compatible provider returns malformed JSON.

## Acceptance Criteria

- `cerebro generate` prints human-readable stage progress to stderr and retains its final JSON result on stdout.
- Starting a web run returns immediately; its event stream can replay events and terminates after success or failure.
- A second start while a run is active receives a typed conflict that identifies the existing run.
- A successful run produces a separately validated candidate whose graph and objects are available to the UI, while the active bundle remains unchanged.
- Failed runs return a sanitized stage error and never expose a candidate preview.
- Semantic Constellation can start a run, narrate every stage, preview the candidate, inspect its objects, and return to the active graph.
- The generation experience is a fixed guided action, not a free-form model prompt and not an activation or editing surface.
- Automated tests use mocked or credential-free providers and never call a developer's live endpoint.

## Boundaries

Runs and event history are in-memory and local to one server process; completed candidate artifacts remain on disk. Specification 010 extends this workflow with database-only discovery, whole-candidate review, and explicit activation. Durable jobs, authentication, candidate editing, schema DDL, and remote orchestration remain out of scope.
