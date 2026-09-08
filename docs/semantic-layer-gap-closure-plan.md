# Close remaining Upgrade Plan gaps

## Context

A prior audit compared the codebase on `feature/upgrade_semantic_layer` against
`Cerebro_Semantic_Layer_and_OKF_Upgrade_Plan.md` (56 sections). The data model,
validator, compiler, linker, and retrieval layers already match the plan closely
(typed `Entity`/`Dimension`/`Metric`/`BusinessRule`/`Relationship` objects, OKF v0.2
conformance, type-aware retrieval). Four gaps remain:

1. **No semantic-then-physical query planner** — the plan's central architectural
   bet (§26/§29/§56: resolve business meaning *before* physical schema) isn't
   implemented. `chat.py` still lets the LLM free-write SQL directly from a flat,
   untyped `QueryPlan`, validated only *after* the fact by `SQLGuardrail`. This is
   the most important gap — everything else in the semantic layer is scaffolding
   built toward a planner that doesn't exist yet.
2. **Graph UI still renders relationships as nodes**, not pure edges (§34), even
   though the backend already builds the equivalent direct entity↔entity edges —
   today's graph is a redundant hybrid.
3. **Hardcoded personal absolute paths** in `config/bank-source.yaml` block
   portability/demo-on-another-machine (§51).
4. **Relationship physical/cardinality parsing is duplicated** ad hoc in three
   places (`bundle.py`, `retrieval.py`, `chat.py`) — not a plan-doc requirement on
   its own, but a planner needs this logic a fourth time, so it gets folded into
   gap 1 as a first sub-task rather than treated separately.

Goal: close gaps 1–3 (with 4 as part of 1), in priority order, while keeping the
existing candidate→review→activation workflow, `SQLGuardrail` safety boundary, and
existing test contracts intact or deliberately/visibly updated.

## Gap 1 — Semantic-then-physical query planner (highest priority)

### 1.0 Factor out relationship parsing (closes gap 4)

New file `src/cerebro/semantic/relationships.py`:
- `RelationshipEndpoint(BaseModel)` — `table: str`, `column: str`
- `ResolvedRelationship(BaseModel)` — `id`, `semantic_from`, `semantic_to`,
  `physical_source: RelationshipEndpoint | None`, `physical_target: RelationshipEndpoint | None`,
  `cardinality: str`
- `resolve_relationship(obj: SemanticObject) -> ResolvedRelationship` — tolerant
  parse handling both the nested `cerebro.physical.source/target` dict shape and
  the legacy flat `source_table/source_column/target_table/target_column` shape.
- `shortest_relationship_path(bundle, by_id, source_entity_id, target_entity_id) -> list[str]`
  — the BFS relocated (behavior-preserving move, not a rewrite) from
  `SemanticRetriever._shortest_relationship_path` (`retrieval.py:444-466`), so it's
  usable without a `SemanticRetriever` instance.

Update the three existing duplicate call sites to use these instead of re-parsing:
- `bundle.py::BundleValidator._validate_relationship` (lines 148-177)
- `retrieval.py::SemanticRetriever._build_edges` (63-100) and `_add_relationship_closure` (423-442);
  delete `_shortest_relationship_path` and call the shared function directly
- `chat.py::SQLGuardrail.__init__` (38-59)

This is a behavior-preserving refactor — run existing tests after this step alone,
before adding new planner behavior, to confirm zero drift.

### 1.1 New typed plan models — `src/cerebro/models.py`

Add (re-exported from `semantic/models.py` the same way existing candidate models are):

- `SemanticFilter` — `dimension_id: str`, `operator: Literal[eq,neq,in,not_in,gt,gte,lt,lte,is_null,not_null]`, `value: Any | None`
- `SemanticQueryPlan` — `intent: str`, `requires_query: bool`, `entities: list[str]`,
  `metrics: list[str]`, `dimensions: list[str]`, `rules: list[str]` (informative only
  in v1 — see note below), `filters: list[SemanticFilter]`, `clarification: str | None`.
  This **replaces** the current flat `QueryPlan` (`models.py:340-347`) as the LLM's
  output type — it references typed semantic ids instead of guessed table-name strings.
- `ResolvedColumn` — `table: str` (bare name), `column: str`
- `ResolvedJoin` — `relationship_id`, `left: ResolvedColumn`, `right: ResolvedColumn`, `join_type`
- `ResolvedPredicate` — `source: ResolvedColumn`, `operator`, `value`, `origin: Literal[metric_measure, plan_filter]`
- `ResolvedAggregateMeasure` / `ResolvedRatioMeasure` / `ResolvedMeasure` — mirrors the
  existing `AggregateMeasure`/`RatioMeasure` discriminated union, with `PhysicalColumnBinding`
  swapped for `ResolvedColumn`
- `ResolvedMetric` — `metric_id`, `alias`, `measure: ResolvedMeasure`
- `PhysicalQueryPlan` — `tables: list[str]`, `joins: list[ResolvedJoin]`,
  `metrics: list[ResolvedMetric]`, `dimensions: list[ResolvedColumn]`,
  `filters: list[ResolvedPredicate]`, `row_limit: int`

**Known v1 limitation to state explicitly, not silently skip:** `BusinessRuleCandidate.logic`
is free text, not a typed predicate AST, so `plan.rules` is included in the planner's LLM
prompt context (to shape which metrics/filters/dimensions the LLM picks) but is not
mechanically compiled into SQL. A structured rule-logic representation is a future
enhancement, out of scope here.

### 1.2 New deterministic planner — `src/cerebro/semantic/planner.py`

- `PlanningError(ValueError)`
- `build_physical_plan(plan: SemanticQueryPlan, by_id: dict[str, SemanticObject], row_limit: int) -> PhysicalQueryPlan`
  — pure Python, never calls the LLM; raises `PlanningError` (naming the offending id)
  on any unknown id, missing physical binding, legacy formula-only metric with no
  structured `measure`, or entity pair with no relationship path.
- `render_sql(plan: PhysicalQueryPlan, schema: str = "main") -> str` — deterministic
  SQL assembly via `sqlglot` expression builders (`exp.Select`/`.from_`/`.join`/`.where`/`.group_by`).

Internal helpers: `_resolve_metric`, `_resolve_dimension` (first `physical_mappings`
entry wins in v1 — multi-mapping dimensions are a documented follow-up),
`_required_entities`, `_resolve_joins` (uses §1.0's `shortest_relationship_path` +
`resolve_relationship`), `_resolve_filters`.

### 1.3 `chat.py::ChatOrchestrator.chat()` — new sequence

Replaces the current single 341-line method's steps 2–4 (`chat.py:230-291`):

1. `grounding = self.retriever.grounding(...)` — unchanged.
2. LLM call `"semantic_query_plan"` → `SemanticQueryPlan` (replaces the `"query_plan"` →
   `QueryPlan` call). Prompt instructs the model to choose only ids present in the
   supplied grounding, never invent ids/table/column names.
3. `plan.clarification` / `not plan.requires_query` branches — unchanged shape,
   operating on the new model.
4. **New deterministic step, no LLM:** `physical_plan = build_physical_plan(plan, self.retriever.by_id, settings.query_row_limit)`.
   On `PlanningError` → `status="blocked"` immediately (no LLM repair retry for a
   structurally invalid plan in v1 — a plan-level mistake isn't something free-form
   SQL repair fixes; note as a possible v2 enhancement, not built now).
5. New trace entry `agent="physical_planning"`.
6. **SQL generation becomes deterministic** — `sql = render_sql(physical_plan, settings.database_schema)`
   replaces the second free-form `"sql_proposal"` LLM call entirely. Once the physical
   plan is fully resolved there's no remaining "meaning" decision left for an LLM to
   make; keeping a second SQL-authoring call would reintroduce exactly the
   hallucination risk (wrong table/column/join) this gap exists to eliminate. A
   free-form/LLM-fallback SQL path is out of scope for v1.
7. `safe_sql = self.guardrail.validate(sql)` — **`SQLGuardrail` is kept unchanged**
   as defense-in-depth (it still owns LIMIT clamping) and as the safety boundary for
   any future raw-SQL path; no `sql_repair` retry loop needed for the template path
   (structural problems are already caught by `PlanningError` earlier).
8. `self.executor.execute(safe_sql)` and the final `"database_answer"` LLM call — unchanged.

New trace sequence: `["knowledge_retrieval", "query_planner", "physical_planning", "sql_generation", "validation", "orchestrator"]`.

### 1.4 Test updates (explicit, not incidental)

`tests/test_chat.py`:
- `ChatProvider` mock (lines 14-25) — drop the `SQLProposal` branch; change the
  `QueryPlan` branch to return a canned `SemanticQueryPlan` using real ids from
  `knowledge/bank-workshop/` (e.g. `entity.customer`, `metric.customer-count`,
  `dimension.customer-gender`).
- `test_chat_runs_validated_read_only_query` — update the trace-sequence assertion
  to the new 6-entry list; keep the `response.sql.startswith("SELECT")` check loose
  rather than asserting exact SQL text (it's now template-rendered).
- `test_sql_guardrail_allows_aggregates_and_blocks_sensitive_or_writes` — unaffected,
  keep as-is (regression coverage for the retained guardrail).

New tests:
- `tests/test_semantic_planner.py` — pure unit tests against the real
  `load_validated_bundle(DEFAULT_BUNDLE)`: single-table happy path, multi-table join
  path (cross-checked against the guardrail test's known-good `accounts.customer_id`/
  `customers.customer_id` join), unresolvable-path → `PlanningError`, unknown id →
  `PlanningError`, and — the most valuable check — `render_sql()` output round-trips
  through `SQLGuardrail.validate()` without raising for representative plans
  (aggregate-only, grouped, joined, ratio metric).
- `tests/test_semantic_relationships.py` — unit tests for `resolve_relationship()`
  against both on-disk shapes.

Verification: `pytest tests/test_chat.py tests/test_bundle_validation.py tests/test_retrieval_api_mcp.py tests/test_semantic_planner.py tests/test_semantic_relationships.py -v`, then full `pytest`.

Contained to `chat.py`, `models.py`, and the two new `semantic/` modules — `api.py`/`cli.py`
construct `ChatOrchestrator(...)` with the same signature, no ripple there.

## Gap 2 — Graph UI: relationships as pure edges

Decision: **full removal** of relationship graph nodes (not a toggle) — the
`physical_fk`/`semantic_relationship` edges already carry the same information, and
clicking an edge becomes the new path to relationship detail in the Inspector
(no data lost, since `/api/concepts/{id}` already looks up any bundle object by id,
independent of graph-node presence).

- `models.py` — `GraphEdge` gains `relationship_id: str | None = None`.
- `retrieval.py::_build_edges` (63-112) — stop emitting the two `relationship_endpoint`
  spoke edges; set `relationship_id` on `physical_fk`/`semantic_relationship` edges.
- `retrieval.py::graph()` (225-237) — filter out `profile_kind == "relationship"` objects
  when building `nodes`.
- `apps/web/src/types.ts` — `GraphEdge` gains `relationship_id?: string | null`; drop
  `'relationship_endpoint'` from `GraphEdgeType`.
- `apps/web/src/GraphView.tsx` — remove `relationship_endpoint` styling and the
  `profile_kind === 'relationship'` node-size branch; extend the edge-tap handler to
  call `onSelect(edge.data('relationship_id'))` for `physical_fk`/`semantic_relationship`
  edges (extract as a small testable helper, e.g. `edgeSelectionTarget(edge)`); drop the
  "relationship audit membership" legend line, keep "entity → entity governed relationship".
- `profilePresentation.ts`, `App.tsx`, `Inspector.tsx` — **no changes needed** (Inspector's
  `kind === 'relationship'` branch already renders full detail; layer filtering already
  operates on whatever nodes exist).
- `apps/web/src/GraphView.test.tsx` — remove the `relationship_endpoint` styling test and
  its row in the table-driven edge test; drop the "relationship audit membership" legend
  assertion; add a unit test for `edgeSelectionTarget`.

Verification: `npx vitest run GraphView.test.tsx App.test.tsx`, `pytest tests/test_retrieval_api_mcp.py -k relationship`. Manual: `npm run dev`, confirm no diamond relationship nodes, click a `physical_fk` and a `semantic_relationship` edge and confirm the Inspector opens with full relationship detail, confirm layer-preset toggles and neighborhood-focus highlighting still work.

## Gap 3 — Hardcoded paths in `config/bank-source.yaml` (brief)

- `database_path` (line 3) → replace with the same placeholder convention as
  `.env.example`/README (`/absolute/path/to/workshop.duckdb`), plus a comment noting
  it's overridden by `CEREBRO_DATABASE_PATH` at runtime and should never hold a real path.
- `csv_provenance_path` (line 4) → delete outright (confirmed unused anywhere in `src/`).
- `schema_reference_path` (line 5) → replace with a neutral placeholder string that
  preserves its purpose (a provenance citation, never opened as a file) without a
  personal path.
- `specs/002-bank-source-discovery.md:19` → leave as a historical requirement snapshot;
  no functional change needed there.

Verification: `grep -rn "Code_Beavers_Txt2Sql_WS" config/ src/ apps/` returns nothing; `pytest tests/test_upstream_and_source.py tests/test_generation.py -v`; manually set `CEREBRO_DATABASE_PATH` and confirm `DuckDBSource` still resolves correctly.

## Critical files

- `src/cerebro/chat.py`
- `src/cerebro/models.py`
- `src/cerebro/semantic/planner.py` (new)
- `src/cerebro/semantic/relationships.py` (new)
- `src/cerebro/retrieval.py`
- `src/cerebro/bundle.py`
- `apps/web/src/GraphView.tsx`, `types.ts`, `GraphView.test.tsx`
- `config/bank-source.yaml`
- `tests/test_chat.py` (new: `tests/test_semantic_planner.py`, `tests/test_semantic_relationships.py`)

## Overall verification

1. `pytest` (full suite) after each gap, not just at the end.
2. `cd apps/web && npm test && npm run build`.
3. Manual smoke: run `cerebro serve`, ask a governed chat question that requires a
   join (e.g. "transaction volume by branch"), confirm the trace shows
   `physical_planning` and the SQL executes successfully.
