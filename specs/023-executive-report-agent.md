# 023 — Executive Report Agent (dynamic multi-SQL, PDF export)

## Problem

Spec 008 answers one question with one SQL query at a time. Spec 009 designed a
single-question insight/narration layer with a strict number-verifier, but it was
never implemented (`insight.py`, `facts.py`, `report_check.py` do not exist in
`src/cerebro/`). Nothing in the repo decomposes one request into several
governed questions, runs them in sequence, and composes the results into one
document; nothing renders a document to a downloadable file.

The user wants an agent, reachable from the web app like the existing chat
workspace, that: given a single request (in Vietnamese, either free-text or a
one-click "Executive Summary" preset), figures out **on its own** how many SQL
questions it needs and what they are (e.g. "tỷ lệ gian lận thẻ" should become
~3 sub-questions: by card type, by merchant category, by branch), runs each
sub-question in sequence through the same governed SQL pipeline the existing
chat already uses, then composes every answer plus a synthesized overview
paragraph into one report, exportable as a PDF.

## Goal

A `ReportOrchestrator` that:

1. **Plans** — one schema-bound provider call turns the user's request into an
   ordered, bounded list of self-contained sub-questions (a `ReportPlan`).
2. **Executes** — each planned sub-question runs, in order, through the
   **existing, unmodified** `ChatOrchestrator.chat()` (spec 008/012) — same
   `SQLGuardrail`, same `DuckDBQueryExecutor`, same provider, same
   aggregate-only/sensitivity rules. This is the "multi-SQL reasoning" step.
3. **Synthesizes** — one more schema-bound provider call turns the completed
   sections into a short Vietnamese overview paragraph placed at the top of
   the report.
4. **Renders** — `ReportDocument` → PDF via WeasyPrint.

Exposed through new run/event/download endpoints and a new chat-styled
"Report agent" workspace in `apps/web`.

## Non-Goals

- Reinventing SQL generation, grounding retrieval, or execution. Every planned
  sub-question goes through the unmodified `ChatOrchestrator.chat()` used by
  `/api/chat` today.
- Implementing spec 009's placeholder-only narration + digit-verifier machinery,
  for either section answers or the overview paragraph. The overview stage is
  instructed to restate only figures already present in the section answers it
  is given, but this is a prompt-level instruction, not a mechanically verified
  guarantee. See Assumptions.
- A hardcoded, catalog-driven report type list. There is exactly one entry
  point — a free-text `request` — and the planner decides the section
  breakdown every time, including for the "Executive Summary" preset, which is
  just a canned Vietnamese request string, not a separate code path.
- Parallel section execution. Sections run strictly one after another, matching
  the agent's reasoning model the user asked for; parallelizing independent
  sections is a future optimization.
- Scheduling/recurring reports, auth beyond what the app already has,
  persistence of runs across a server restart.
- Rendering the frontend's Recharts components inside the PDF. PDF tables/values
  are plain HTML/CSS rendered by WeasyPrint.

## Functional Requirements

### Planning

- FR-1: `POST /api/reports/runs` accepts one field, `request` (free Vietnamese
  text, 1–2000 chars). There is no `report_type` enum; the "Tạo báo cáo tổng
  quan điều hành" button in the UI sends a canned Vietnamese request string
  through the same field.
- FR-2: The planner issues one schema-bound provider call
  (`ReportPlan {title, sections: [{title, question}]}`) whose prompt carries
  the user's `request` plus a deterministic, DB-free grounding catalog: every
  active bundle object's `id`, `title`, and short description, filtered to
  entities, metrics, and dimensions. No source row and no DB connection is
  involved in planning.
- FR-3: Planned section count is bounded to `[1, 7]`. A plan with zero sections
  falls back to one section whose `question` is the original `request`
  verbatim. A plan with more than 7 sections is truncated to the first 7, in
  the order the model returned them. This bound is configuration, not a
  hard-coded literal.
- FR-4: Each planned section's `question` must be self-contained (answerable on
  its own, without seeing the other sections or the original request) — the
  planner prompt states this requirement; enforcement is by prompt
  instruction, not a mechanical check, consistent with FR-2's non-goal scope.

### Execution

- FR-5: `ReportOrchestrator` executes each planned section's `question`
  through `ChatOrchestrator.chat()` sequentially, in plan order. Each section
  is an independent request: no `conversation_id` or `history` is carried
  from a prior section, from the plan, or from the original `request`.
- FR-6: A section whose `ChatResponse.status` is `blocked` or `clarification`
  does not abort the run. It is recorded in the `ReportDocument` with that
  status and the returned `answer` text as-is; its `sql`/`columns`/`rows` stay
  empty. One unanswerable section degrades itself, never the whole report.
- FR-7: Overall run status is `completed` when every section is `answered`,
  `partial` when at least one section is `answered` and at least one is
  `blocked`/`clarification`, and `failed` when no section is `answered`. When
  `failed`, the synthesize stage (FR-8) is skipped.

### Synthesis

- FR-8: After every section has run (and at least one is `answered`), one more
  schema-bound provider call (`ReportOverview {summary}`) receives the ordered
  list of `{title, question, status, answer}` for every executed section (not
  raw SQL/rows — narrative text only, to bound prompt size) and returns a
  short Vietnamese paragraph (≤ 800 characters) summarizing the whole report.
  This paragraph is `ReportDocument.overview`.
- FR-9: The synthesize prompt instructs the model to restate only facts
  already present in the section answers it was given, and to name a section
  by title rather than inventing a new figure. This is a best-effort
  instruction; no verifier rejects a non-compliant response (see Assumptions).

### Language

- FR-10: The planner and synthesize prompts instruct Vietnamese output for
  `ReportPlan.title`, each section's `title`/`question`, and
  `ReportOverview.summary`. Section execution (FR-5) inherits whatever
  language the planned `question` is written in; spec 018 already established
  that `ChatOrchestrator.chat()` answers Vietnamese questions correctly live.
  No change to `chat.py` is needed for this.

### Progress and runs

- FR-11: Report runs are managed the same way generation runs are
  (`src/cerebro/generation_runs.py::GenerationRunManager`): an in-memory
  `ReportRunManager` starts a run on a background thread, exposes `start`,
  `get`, `events_after`, and `document`, and assigns a monotonic `sequence` to
  each emitted event. Events are emitted for: `planning` started/completed,
  each section id started/completed/blocked, and `synthesizing`
  started/completed.
- FR-12: `GET /api/reports/runs/{run_id}/events` streams those events over
  SSE, mirroring `/api/generation/runs/{run_id}/events`'s framing (`event:
  progress` per stage, a terminal `event: complete` carrying the full run).
  SSE is required, not optional, because a run may involve up to 9 sequential
  provider calls (1 plan + up to 7 sections + 1 synthesis), and spec 018
  already measured single chat questions taking 100–240+ seconds — a single
  blocking HTTP call for the whole run risks a proxy/browser timeout.
- FR-13: Multiple report runs may execute concurrently; `ReportRunManager.start`
  never raises a conflict for an in-flight run, because sections are
  read-only and `ChatRequestRegistry` already lets concurrent chat requests
  coexist safely.

### Cancellation

- FR-13a: `ReportRunManager` reuses `src/cerebro/chat.py::ChatRequestRegistry`
  directly, keyed by `run_id` instead of a chat `request_id`, to obtain one
  `ChatCancellation` per run at `start()` time.
- FR-13b: `ReportOrchestrator.run` accepts that `ChatCancellation` and checks
  `.checkpoint()` between the planning call, before/after each section, and
  around the synthesize call, and forwards it into every
  `ChatOrchestrator.chat()` call so a section already in flight is
  cancellable too — the same cooperative-cancellation depth
  `ChatOrchestrator.chat()` already gives `/api/chat`, not a new mechanism.
  A live provider HTTP call already sent for planning or synthesis is not
  aborted mid-flight (matching the pre-existing limitation of spec 008's own
  `_generate`, which does not use `register_interrupt`); cancellation takes
  effect at the next checkpoint once that call returns.
- FR-13c: `POST /api/reports/runs/{run_id}/cancel` calls
  `ReportRunManager.cancel(run_id)`, mirroring
  `/api/chat/requests/{request_id}/cancel`; it returns `202` unconditionally,
  including for an unknown or already-terminal `run_id` (idempotent, like the
  chat endpoint it mirrors).
- FR-13d: A cancelled run's status is `cancelled` (added to `ReportRun.status`
  and treated as terminal by `events_after`). No `ReportDocument` and no PDF
  are ever produced for a cancelled run, since `ReportOrchestrator.run` raises
  `ChatCancelled` before returning one; `GET .../document` and `GET .../pdf`
  both respond `409` for a `cancelled` run, same as for `failed`.
- FR-13e: The frontend shows a "Dừng" (Stop) action in the report panel's chat
  heading only while a run is in progress, calls the cancel endpoint, and
  relies on the existing SSE `complete` event (now potentially carrying
  `status: "cancelled"`) to update the transcript — no new endpoint is polled
  for this.

### PDF rendering

- FR-14: `render_report_pdf(document: ReportDocument) -> bytes` in
  `src/cerebro/report_pdf.py` is a pure function: no model call, no DB
  connection, no network. It renders a Jinja2 HTML template and converts it to
  PDF bytes with WeasyPrint.
- FR-15: The PDF opens with a header block (report `title`, `generated_at`,
  `semantic_version`, overall run status) followed by the `overview`
  paragraph, then every section in plan order: section title, narrative
  `answer`, a data table of `columns`/`rows` (capped at 25 rendered rows with
  a "N dòng không hiển thị" note when `row_count` exceeds that), the executed
  `sql` in a monospace block, and any `warnings`.
- FR-16: A section with `row_count == 0` renders an explicit "Không có dữ liệu
  phù hợp" line, never an empty table. A section with `truncated = true`
  renders a truncation note.
- FR-17: `GET /api/reports/runs/{run_id}/pdf` returns `200` with
  `Content-Type: application/pdf` and `Content-Disposition: attachment;
  filename="report-<run_id>.pdf"` once the run is `completed` or `partial`.
  It returns `409` while the run is `running`, and `409` with a reason when
  the run is `failed` or `cancelled`.

### Frontend workspace

- FR-18: `apps/web` gains a third workspace value (`Workspace = 'semantic' |
  'text-to-sql' | 'report'`) with its own entry in `WorkspaceMenu`, alongside
  a new `ReportPanel.tsx`.
- FR-19: The panel presents a chat-styled transcript. A free-text composer
  plus a preset dropdown (a curated list of report requests, e.g. executive
  summary, credit risk, card fraud, branch performance) and a confirm button
  both submit to the same `request` field. As events arrive: the plan (list
  of section titles) appears first, then each section appears as its event
  arrives (question asked → SQL used → answer), then the overview paragraph.
  While a run is in progress, a "Dừng" (Stop) action sits next to the chat
  heading, next to where the in-progress output is rendered (see FR-13e).
- FR-19a: Once a run reaches a terminal state, the live per-stage progress
  view (plan → sections → synthesis) is not discarded: the finished result
  keeps a collapsible "Quá trình thực hiện" trace section, derived from the
  same `ReportRun.events` the live view used, so the sequential execution
  order remains inspectable after the fact, not only while it is happening.
- FR-20: Once the run reaches a terminal, non-cancelled state, a persistent
  "Xuất PDF" control appears bound to that `run_id` and calls the download
  endpoint.

## Acceptance Criteria

- AC-1: `POST /api/reports/runs {"request": "..."}` returns `202` with a
  `run_id`; polling `GET /api/reports/runs/{run_id}` eventually yields
  `status` in `{completed, partial, failed, cancelled}`.
- AC-2: With a fixture provider returning a canned `ReportPlan` of 3 sections
  for a fraud-themed request, and canned `QueryPlan`/`SQLProposal`/
  `AnswerPayload` per section question, the resulting `ReportDocument` has
  exactly those 3 sections, in plan order, each carrying that section's canned
  SQL/answer, and a non-empty `overview` from the canned `ReportOverview`.
- AC-3: A fixture plan with 0 sections yields exactly 1 section whose
  `question` equals the original `request`. A fixture plan with 12 sections
  yields exactly the first 7.
- AC-4: A fixture section that returns a `clarification` `ChatResponse` yields
  a document section with `status = "clarification"`; later sections still
  run; overall run `status = "partial"`; the synthesize stage still runs
  because at least one section is `answered`.
- AC-5: An all-`blocked` fixture yields `status = "failed"` and the synthesize
  stage is skipped (no `ReportOverview` provider call recorded).
- AC-6: `render_report_pdf` on a fixture `ReportDocument` returns bytes
  starting with `%PDF`, and the rendered text contains the overview
  paragraph, every section title, and every row value from every section's
  table.
- AC-7: `GET /api/reports/runs/{run_id}/pdf` returns `409` before the run is
  terminal, and `200` with `application/pdf` + `Content-Disposition:
  attachment` after `completed`/`partial`.
- AC-8: `GET /api/reports/runs/{run_id}/events` emits a `planning` event, one
  event per section in order, a `synthesizing` event, then a terminal
  `complete` event carrying the full run.
- AC-9: The full backend suite passes with no live API key, no network,
  fixture providers only — same convention as the rest of the repo.
- AC-10: `npm run build` and `npx vitest run` succeed in `apps/web` after the
  new panel and workspace wiring are added.
- AC-11: Calling `ReportRunManager.cancel(run_id)` while a run is in progress
  eventually yields `status = "cancelled"`; no `ReportDocument` and no PDF are
  ever produced for that run (`document()`/`pdf()` raise `ReportNotReady`, and
  the HTTP endpoints respond `409`).
- AC-12: The finished-report view (`ReportPanel`) still exposes the ordered
  plan → sections → synthesis trace after the run completes, not only while
  it was running.

## Edge Cases

- EC-1: Database unavailable (mirrors `/api/chat`'s `blocked` path) → every
  section is `blocked`, run `status = "failed"`, synthesize skipped, PDF
  endpoint returns `409`.
- EC-2: Planner returns a section whose `question` cannot be grounded (chat
  resolves it to `clarification`) → FR-6 handling, not a run failure.
- EC-3: A section result is `truncated` → PDF note per FR-16.
- EC-4: Two concurrent `POST /api/reports/runs` calls → two independent
  `run_id`s, independent event streams, no cross-run state leakage.
- EC-5: A section whose query legitimately returns zero rows → FR-16
  rendering, not an error.
- EC-6: Planner output fails schema validation / provider error → the whole
  run fails with a stated reason (mirrors `ChatOrchestrator`'s existing
  provider-error handling), no partial plan is executed.
- EC-7: `POST .../cancel` on an already-terminal or unknown `run_id` is a
  no-op that still returns `202` (mirrors the chat cancel endpoint, which
  never errors on a stale or unknown ID).
- EC-8: Cancellation lands between two sections (not mid-section) →
  `ReportSectionResult`s for sections that already finished are simply lost
  (no partial `ReportDocument` is ever built for a cancelled run, by FR-13d);
  only the SSE events already emitted for those sections remain visible.

## Interfaces / Contracts

```python
class ReportPlanSection(BaseModel):
    title: str
    question: str


class ReportPlan(BaseModel):
    title: str
    sections: list[ReportPlanSection] = Field(default_factory=list)


class ReportOverview(BaseModel):
    summary: str


class ReportSectionResult(BaseModel):
    id: str                    # s1, s2, ... assigned by the orchestrator
    title: str
    question: str
    status: Literal["answered", "clarification", "blocked"]
    answer: str
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReportDocument(BaseModel):
    run_id: str
    request: str
    title: str
    overview: str = ""
    generated_at: str
    semantic_version: str
    status: Literal["completed", "partial", "failed"]
    sections: list[ReportSectionResult] = Field(default_factory=list)


class ReportRunRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)


class ReportRun(BaseModel):
    run_id: str
    request: str
    status: Literal["running", "completed", "partial", "failed", "cancelled"]
    started_at: str
    completed_at: str | None = None
    current_stage: str | None = None   # "planning" | section id | "synthesizing"
    error: str | None = None
    events: list["ReportEvent"] = Field(default_factory=list)


class ReportEvent(BaseModel):
    sequence: int
    stage: str                  # "planning" | section id | "synthesizing"
    status: Literal["started", "completed", "clarification", "blocked", "failed"]
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
```

`status` mirrors `ReportSectionResult.status` for a section event (so a
`clarification` section reports itself accurately, not silently coerced to
`blocked` or `completed`), plus `started`/`failed` used by non-section stages.

API surface (new):

| Method & path | Purpose |
|---|---|
| `POST /api/reports/runs` | Start a run for `request`; `202` + `ReportRun` |
| `POST /api/reports/runs/{run_id}/cancel` | Request cancellation; `202`, mirrors `/api/chat/requests/{id}/cancel` |
| `GET /api/reports/runs/{run_id}` | Poll `ReportRun` status |
| `GET /api/reports/runs/{run_id}/events` | SSE progress, mirrors generation events |
| `GET /api/reports/runs/{run_id}/document` | Full `ReportDocument` once terminal |
| `GET /api/reports/runs/{run_id}/pdf` | Download rendered PDF |

Module boundaries:

| Module | Responsibility | Needs a model? |
|---|---|---|
| `src/cerebro/report_agent.py` | `ReportOrchestrator`: plan (FR-2 to FR-4), execute (FR-5 to FR-7), synthesize (FR-8, FR-9) | yes, via the same provider `ChatOrchestrator` already holds |
| `src/cerebro/report_runs.py` | `ReportRunManager`, FR-11 to FR-13e | no |
| `src/cerebro/report_pdf.py` | `render_report_pdf`, FR-14 to FR-16 | no |
| `apps/web/src/ReportPanel.tsx` | FR-18 to FR-20 | no |

## Constraints

- Introduces no new SQL-generation/execution path, no new DuckDB access path,
  no new credential — every section is answered by the unmodified spec
  008/012 `ChatOrchestrator`. Planning and synthesis reuse the same provider,
  key, and schema-binding transport spec 008 already uses.
- New Python dependencies: `weasyprint`, `jinja2` (added to `pyproject.toml` and
  `uv.lock`, which the runtime image installs from via `uv sync --frozen`).
- WeasyPrint (v53+) needs Pango/Cairo for text shaping plus at least one
  installed font — it no longer needs GDK-Pixbuf for PDF output (Pillow, a
  transitive Python dependency, handles raster images instead). Verified
  locally via `brew install pango` (macOS), which renders a real PDF end to
  end. The `Dockerfile`'s runtime stage installs the Debian equivalents:
  `libpango-1.0-0 libpangocairo-1.0-0 fonts-dejavu-core` — the font package
  is required, not optional, since a slim base image ships no fonts and
  WeasyPrint would otherwise have nothing to shape text with.
- Report runs are in-memory only, like `GenerationRunManager` today; a server
  restart loses in-flight and completed run state.
- Worst-case latency is high by construction (up to 9 sequential provider
  calls). No automatic timeout is added; a very slow run simply takes a long
  time, observably, over SSE, unless the user cancels it (FR-13a to FR-13e).
- No automatic timeout is added in v1 — a stuck run must be cancelled by the
  user (or outlive the process, since state is in-memory only, per the bullet
  above). Cancellation itself is cooperative at the same granularity
  `ChatOrchestrator.chat()` already offers `/api/chat`: a live provider HTTP
  call already in flight for planning or synthesis is not aborted mid-request
  (no new preemption mechanism is introduced beyond what spec 008 has).

## Assumptions

- The overview paragraph (FR-8, FR-9) and the per-section `answer` text both
  rely on prompt-level instruction, not a mechanical verifier, to avoid
  inventing figures — this repo's stricter design for that problem (spec 009)
  exists but was deliberately not implemented here, by explicit user decision,
  to ship a working multi-SQL report agent in this session. If that rigor is
  wanted later, spec 009 should be implemented and each section's answer
  replaced with its `InsightReport`.
- Vietnamese is the default output language throughout. Spec 018 already
  proved `ChatOrchestrator.chat()` answers Vietnamese questions correctly;
  this spec does not need to touch `chat.py` for language support.
- The active `data/workshop.duckdb` was found empty (0 rows in all 10 tables)
  during discovery, while `database/*.csv` holds the real seed data (up to 3M
  rows in `card_transactions`). This spec assumes that data-loading step
  happens separately (`cerebro scan`/load) before a report run produces
  non-trivial output; it is out of scope here.
- The grounding catalog fed to the planner (FR-2) is built from bundle object
  metadata already held in memory (`SemanticBundle.objects`); no additional
  retrieval call is introduced for planning.

## Open Questions

- Whether the planner should be allowed to reuse `SemanticRetriever` (the same
  ranking used per-question) instead of a flat full-bundle listing, if the
  bundle grows large enough that a flat listing stops fitting the prompt
  budget. Deferred; the current bundle is small enough that this is not yet a
  problem.
- Whether cross-run section reuse/caching (two runs asking near-identical
  sub-questions) is worth adding. Deferred.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-1 | FR-2, FR-3, AC-3 | Fixture provider returns 0-section and 12-section `ReportPlan`s; orchestrator normalizes to 1 (echoing `request`) and 7 sections respectively. |
| T-2 | FR-5, AC-2 | Fixture provider keyed per section `question`; `ReportOrchestrator.run` produces sections in plan order, each with that section's canned SQL/answer; `chat()` called once per section with no shared `conversation_id`. |
| T-3 | FR-6, FR-7, AC-4 | One fixture section returns `clarification`; document keeps that status, later sections still populated, run `status = "partial"`, synthesize stage still invoked. |
| T-4 | FR-7, FR-8, AC-5 | All-fixture-`blocked` sections yield `status = "failed"`; provider's `ReportOverview` schema is never requested. |
| T-5 | FR-8, FR-9 | Synthesize call's prompt includes every section's title/question/status/answer and excludes raw SQL/rows. |
| T-6 | FR-11, FR-12, AC-8 | `ReportRunManager` emits `planning`, per-section, `synthesizing`, then a terminal `complete` event; SSE stream test mirrors the existing generation-events test. |
| T-7 | FR-13, EC-4 | Two concurrent `start()` calls produce two independent `run_id`s and independent event cursors. |
| T-8 | FR-14, FR-15, FR-16, AC-6 | `render_report_pdf` on a fixture `ReportDocument` returns `%PDF`-prefixed bytes; extracted text contains the overview, every section title, header block, and every row value. |
| T-9 | FR-16, EC-3, EC-5 | Zero-row section renders "Không có dữ liệu phù hợp"; truncated section renders the truncation note. |
| T-10 | FR-17, AC-7 | PDF endpoint: `409` while running, `409` on `failed`, `200` + correct headers on `completed`/`partial`. |
| T-11 | EC-1 | No DuckDB configured → all sections `blocked`, run `failed`, synthesize skipped, PDF `409`. |
| T-12 | EC-6 | Provider raises / returns schema-invalid `ReportPlan` → run `status = "failed"` with a stated `error`, no sections executed. |
| T-13 | FR-18, FR-19, FR-20, AC-10 | Frontend: `ReportPanel` renders plan/section/overview progress from a mocked SSE stream and shows "Xuất PDF" only once the run is terminal, non-cancelled; `npm run build` and `vitest run` pass. |
| T-14 | FR-13a to FR-13d, AC-11 | `ReportRunManager.cancel()` on an in-flight stub run (blocked on a checkpoint) yields `status = "cancelled"`; `document()`/`pdf()` raise `ReportNotReady`; the run's event list reflects only what ran before cancellation. |
| T-15 | FR-13e, AC-11 | Frontend: clicking "Dừng" while a run is pending calls the cancel endpoint with the active `run_id`; a `complete` SSE event carrying `status: "cancelled"` renders a dedicated message and never triggers a document fetch. |
| T-16 | FR-19a, AC-12 | Frontend: after a run's `document` loads, the rendered result still exposes a "Quá trình thực hiện" trace derived from `ReportRun.events`, covering planning, each section, and synthesis. |

## Addendum — Report presentation redesign (2026-09-12)

Presentation-only follow-up: the original FR-18/FR-19/FR-20 UI shipped by reusing
chat-message styling verbatim, which reads as one more chat bubble rather than a
report (no status-at-a-glance, no section numbering, no way to jump around a long
report). No new data, endpoint, or contract is introduced — every element below is
derived from fields `ReportDocument`/`ReportSectionResult`/`ReportRun` already
expose.

- FR-21: `ReportPanel` renders a dedicated header for a finished/failed document
  (title, a color-coded status badge per `document.status`, formatted
  `generated_at`, section count, the existing "Xuất PDF" action restyled as a
  primary button, and a new "Sao chép" action that copies a plain-text rendering
  of the title/overview/sections to the clipboard) — replacing the current inline
  `chat-message-head` treatment.
- FR-22: The overview paragraph is presented as a labelled "Tóm tắt điều hành"
  callout distinct from section content, not a bare paragraph.
- FR-23: Each `ReportSectionResult` renders as a numbered card carrying a
  color-coded status badge for `answered`/`clarification`/`blocked`, its
  `question` as a chip, and — when `status === "answered"` — an explicit row-count
  caption (`row_count`, plus a truncation note when `truncated`) before the
  existing `ResultPanel` table/chart; a `row_count === 0` section shows an
  explicit "Không có dữ liệu phù hợp" line instead of an empty table, mirroring
  the PDF's existing FR-16 behavior on the web side too.
- FR-24: When a report has 3 or more sections, a "Mục lục" (table of contents)
  lists every section title as an anchor link that scrolls to that section's
  card within the same transcript message.
- FR-25: The in-progress view (`ReportProgress`) gains a horizontal stepper
  (Lên kế hoạch → each section → Tổng hợp) derived from the same `ReportRun.events`
  the existing per-stage detail list already uses, so progress is scannable at a
  glance; the existing descriptive per-stage list (FR-11's events) is kept
  alongside it, not replaced.
- FR-26: `render_report_pdf` (`src/cerebro/report_pdf.py`) gains: a color-coded
  status badge per section (mirroring FR-23's web badge), a page-number footer,
  native PDF outline bookmarks (`bookmark-level`/`bookmark-label` on the title and
  each section heading, so PDF viewers show a navigable outline pane), and — when
  a report has 3 or more sections — an in-document "Mục lục" listing linking to
  each section's anchor. No change to the pure-function contract of FR-14
  (still no model/DB/network call), and no new fields on `ReportDocument`/
  `ReportSectionResult`.

### Addendum acceptance criteria

- AC-13: A finished `ReportDocument` with `status = "partial"` renders a header
  whose status badge reads the partial label, distinct in color from a
  `completed` or `failed` badge.
- AC-14: A document with 2 sections renders no "Mục lục"; a document with 3+
  sections renders one link per section, and clicking a link scrolls that
  section's card into view.
- AC-15: A section with `status = "answered"` and `row_count = 0` renders
  "Không có dữ liệu phù hợp" and no `ResultPanel` table; a section with
  `truncated = true` shows a truncation note next to its row count.
- AC-16: `render_report_pdf` on a fixture `ReportDocument` with 3+ sections
  produces bytes starting with `%PDF` whose extracted text still contains every
  element required by AC-6, plus each section's status label rendered as
  distinguishable text (badge content survives text extraction even though color
  does not).

### Addendum test design

| Test | Requirement | Verification |
|---|---|---|
| T-17 | FR-21, AC-13 | Frontend: rendered header shows a status badge whose label/class differs across `completed`/`partial`/`failed` documents. |
| T-18 | FR-23, AC-15 | Frontend: an `answered` section with `row_count = 0` renders the empty-state line and no table; a `truncated` section shows the truncation note. |
| T-19 | FR-24, AC-14 | Frontend: a 2-section document renders no TOC nav; a 3-section document renders a TOC with one link per section. |
| T-20 | FR-26, AC-16 | `render_report_pdf` on a fixture 3-section `ReportDocument` still returns `%PDF`-prefixed bytes containing every section's status label text. |
