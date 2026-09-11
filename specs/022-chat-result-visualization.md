# 022 — Chat result dashboard visualization and saved charts

## Goal

After a Text-to-SQL chat query returns tabular results, automatically render a small chart next
to (never replacing) the existing result table whenever the shape of `columns`/`rows` supports
one, laid out side by side rather than stacked. Let a user save a question together with its
frozen result/chart so it can be reopened later from a list in the chat sidebar, without
re-executing the query. Separately, fix a visual misalignment between the composer's send
button and its textarea. Chart selection and rendering are entirely client-side; saved charts
are a new small backend-persisted store. No change to the `/api/chat` `ChatResponse` contract.

## Functional requirements

### Column inference and chart selection

- FR-2201: A pure function classifies each result column as `numeric`, `temporal`, or
  `categorical` from its cell values only (no server-supplied type metadata exists). A column
  where every non-null cell is a finite number is `numeric`. A column where every non-null cell
  is a string matching a restrictive ISO-date/datetime pattern and parseable by `Date.parse` is
  `temporal`. Everything else is `categorical`.
- FR-2202: A column containing any boolean value is always `categorical`, never `numeric`.
- FR-2203: A column whose cells are all `null` is `categorical`.
- FR-2204: A column mixing types across non-null cells (e.g. some numbers, some strings) is
  `categorical` — classification never guesses across mixed evidence.
- FR-2205: A chart-selection function resolves `{columns, rows}` to one of `kpi`, `bar`, `line`,
  `grouped-bar`, `multi-line`, or `null` (no chart) as follows:
  - `null` when: fewer than 2 rows, OR zero numeric columns.
  - `kpi`: exactly 1 row and 1+ numeric columns — one tile per numeric column.
  - `bar`: exactly 1 categorical column + exactly 1 numeric column, 2+ rows.
  - `line`: exactly 1 temporal column + exactly 1 numeric column, 2+ rows, points sorted
    chronologically.
  - `grouped-bar` / `multi-line`: exactly 1 non-numeric column (categorical → grouped-bar,
    temporal → multi-line) + 2+ numeric columns, 2+ rows.
  - `null` for every other shape (e.g. 2+ non-numeric columns).
  - A categorical axis is never rejected purely for having many distinct values (e.g. dozens of
    branches) — FR-2206's plotted-row cap keeps the chart readable in that case instead.
- FR-2206: When more than 30 rows would be plotted, only 30 are plotted and the chart shows a
  note naming how many of the total rows are shown — distinct from the existing "Results
  truncated by the governed row cap" note, which reflects the SQL row cap, not the chart's
  display cap. Which 30 are kept depends on the axis: chronological order for a temporal axis
  (earliest 30); for a categorical axis, the 30 highest-value rows by the first numeric column
  (a "top 30" view), so a large category count degrades to the most significant rows rather than
  an arbitrary or no chart. Below the cap, row order is left exactly as returned.
- FR-2207: Chart series use a fixed palette in column order — `#3987e5`, `#d95926`, `#199e70`,
  `#c98500`, `#d55181` — validated against this app's dark background. A single-measure chart
  (`bar`/`line`) uses the first color only; the app's existing UI accent colors
  (`--cyan`/`--violet`/`--amber`/`--rose`) are never used as chart series colors.
- FR-2208: No chart ever uses two y-axis scales; `grouped-bar`/`multi-line` share one linear
  scale across all measures.
- FR-2209: A legend renders if and only if the chart is `grouped-bar` or `multi-line`.
- FR-2210: Every bar, KPI tile, and line/point mark exposes a hover tooltip showing its
  category/date label and exact value; line charts also show a crosshair on hover.

### Layout

- FR-2211: The existing result table's markup, `.result-table-wrap` class name, and truncation
  note text are unchanged. When a chart is selected, the table and the chart render as sibling
  panels in a responsive grid that shows them side by side when there is room and stacks them
  automatically on narrow viewports; when no chart is selected, only the table renders, occupying
  the same single-column footprint as before this change.
- FR-2212: The chat area and an assistant message carrying results are allowed to use more
  horizontal space than before, so the side-by-side layout is not cramped on typical viewport
  widths.
- FR-2219: When shown side by side, the table panel and the chart panel are the same overall
  height — a table with more rows than fit in that height scrolls internally (its header stays
  pinned) rather than growing taller than the chart panel next to it.

### Saved charts

- FR-2213: A `SavedChart` snapshot — `id`, `question`, `sql`, `columns`, `rows`, `row_count`,
  `truncated`, `created_at` — can be created, listed, and deleted through a small file-backed
  store, one JSON file per saved item under `knowledge/saved_charts/` (gitignored, mirroring the
  existing `knowledge/reviewed/` runtime-state convention). There is no per-user ownership —
  saved charts are global, consistent with this app having no user-account concept.
- FR-2214: `POST /api/saved-charts` creates and returns a `SavedChart` (server-assigned `id` and
  `created_at`); `GET /api/saved-charts` returns all saved charts, newest first; `DELETE
  /api/saved-charts/{id}` removes one, returning 404 (`unknown_saved_chart`) for an unknown or
  path-traversal identifier and 200 with `{"deleted": "<id>"}` otherwise.
- FR-2215: Listing tolerates a corrupted or unparseable saved-chart file by skipping it rather
  than failing the whole listing.
- FR-2216: A save action is available on any answered chat result (`status === "answered"` and
  at least one column) and captures the paired question, the executed SQL, and the exact
  columns/rows/row_count/truncated returned at that time.
- FR-2217: The chat sidebar shows a "Saved" list (question, saved time, view and delete actions)
  populated from `GET /api/saved-charts`. Opening a saved item appends it to the current chat
  transcript using its frozen snapshot — it never re-executes the question through `/api/chat`.
  Deleting a saved item is gated behind an inline confirmation step.

### Composer

- FR-2218: The chat composer's send button is vertically centered against its textarea at the
  textarea's default (and any manually resized) height, rather than bottom-aligned.

## HTTP contract additions

- `POST /api/saved-charts` — body `{question, sql, columns, rows, row_count, truncated}` →
  `SavedChart` (adds `id`, `created_at`).
- `GET /api/saved-charts` — `SavedChart[]`, newest first.
- `DELETE /api/saved-charts/{chart_id}` — `{"deleted": "<chart_id>"}` on success.

## Test matrix

| ID | Requirement | Verification |
|---|---|---|
| T-2201 | Column kind inference | Unit tests: all-null → categorical; mixed types → categorical; boolean column → categorical; ISO date strings → temporal; plain numbers → numeric. |
| T-2202 | Bar case | 1 categorical + 1 numeric column, 2+ rows resolves to `bar` with a single-color series. |
| T-2203 | Line case | 1 temporal + 1 numeric column resolves to `line` with points in chronological order. |
| T-2204 | Multi-series case | 1 axis column + 2 numeric columns resolves to `grouped-bar`/`multi-line` with 2 series in column order and a legend. |
| T-2205 | KPI case | 1 row with 1+ numeric columns resolves to `kpi` with one tile per numeric column. |
| T-2206 | No-chart cases | 2 categorical columns, or zero numeric columns, resolves to `null`. |
| T-2207 | Row cap | A 40-row temporal result plots the earliest 30 rows; a 73-distinct-category result plots the top 30 by value (highest first) — both report the omitted count instead of returning `null`. |
| T-2208 | Table preserved | For every eligible case, `.result-table-wrap`'s row/column count matches the response exactly; the chart never replaces or truncates the table. |
| T-2209 | Side-by-side layout | An eligible result renders the table and chart as two sibling panels in `.result-panels`; an ineligible result renders only the table. |
| T-2210 | Saved-chart round trip | `SavedChartStore.create(...)` then `.list()` returns it; `.delete(id)` then `.list()` no longer returns it. |
| T-2211 | Saved-chart guards | `delete()` on an unknown or path-traversal identifier raises `SavedChartNotFound`; `list()` skips a corrupted file instead of raising. |
| T-2212 | Saved-chart HTTP mapping | `DELETE /api/saved-charts/{unknown}` → 404 `unknown_saved_chart`; a real id → 200 and absent from a follow-up `GET /api/saved-charts`. |
| T-2213 | Save and reopen UI | Clicking Save posts the paired question/SQL/results; clicking a sidebar item appends the frozen snapshot to the transcript with no new `/api/chat` call. |
| T-2214 | Composer alignment | The composer's send button and textarea are vertically centered relative to each other in the rendered layout. |
| T-2215 | Equal panel height | A 100-row table result renders its table and chart panels at the same height, with the table scrolling internally instead of extending past the chart. |

## Boundaries

No change to `/api/chat`'s `ChatResponse` contract or to `ChatOrchestrator`/`DuckDBQueryExecutor`.
No new npm dependency — charts are hand-rolled inline SVG. No changes to the unrelated
`/api/agent` Text2SQL spec-008 subsystem, which is not wired to any frontend UI. Saved charts
have no per-user ownership, no edit/update operation, and no re-execution path — they are an
immutable, deletable snapshot. No chart types beyond KPI/bar/line/grouped-bar/multi-line in this
iteration.
