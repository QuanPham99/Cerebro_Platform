# 026 — Scalable Semantic Graph Navigation

## Problem

The Semantic Constellation graph (`apps/web/src/GraphView.tsx`, backed by `GET /api/graph`)
renders the entire active bundle flat in one Cytoscape.js canvas: every entity, dimension,
metric, rule, relationship, table, and policy at once, one full force-directed layout on the
main thread, and every edge visible by default. That is workable at the current demo scale
(~76 objects before this spec), but the product goal is a graph that stays usable at "thousands
of tables, entities, metrics, and relationships" for real banking schemas, and nothing in the
loader, retriever, graph API, or frontend had a mechanism to avoid rendering everything at once.
There was also no grouping tier above `entity` — `profile_kind` was a flat nine-way enum.

## Goal

A map-like, progressively-disclosed graph: a bounded default overview (domain + entity only),
explicit 1-hop expansion around a node, a true Focus Mode isolate, Find Path between two nodes,
hidden-by-default edges revealed on interaction, zoom-based level of detail, relationship-type
and column-search filters, and off-main-thread layout for graphs too large for a synchronous
force layout — without regressing the existing knowledge-graph UI (spec 006) or the customer
self-service scoping (spec 024).

## Non-Goals

- Rendering individual columns as graph nodes. Spec 006 FR-506 is reaffirmed, not reversed —
  "Table → Column" progressive disclosure means richer, searchable column detail in the
  Inspector when a table is reached, never literal column nodes on the canvas.
- Replacing Cytoscape.js with a WebGL-native rendering engine. The bounded default view and
  1-hop expansion keep the rendered working set small in the common case; Cytoscape's own canvas
  renderer plus a Worker-computed layout (§FR-11) covers the large-graph case. This also matches
  spec 024's explicit "no new graph engine" precedent for the customer-scoped graph reuse.
- A general-purpose pagination API. `tier`/`node_id`/`depth` on `GET /api/graph` are the only new
  query shapes; no cursor/offset pagination was added.
- Extending `/api/golden/graph`, `/api/bundles/{id}/graph`, `/api/generation/runs/{id}/graph`, or
  `/api/definition-revisions/{id}/graph` with tier/depth params. Those serve already-small,
  already-bounded preview graphs (one generation run or bundle version); only the primary
  `/api/graph` route gained progressive-disclosure params.
- Community-detection or algorithmic clustering. "Collapse related nodes into semantic clusters"
  is satisfied by the domain/entity tier itself (a curated, human-authored grouping), not a
  computed graph-clustering algorithm.
- Making the frontend's *default live-workspace* fetch itself bounded. `apps/web/src/App.tsx`
  fetches the full graph once (`getGraph({ tier: 'all' })`) and applies the domain/entity default
  as a client-side type filter, because chat-grounding highlighting (`usedIds`), the keyboard
  navigator's full-kind browsing, and free-text search over every object all depend on the
  complete object set already being resident client-side. The backend's `tier`/`node_id`/`depth`
  params are real, tested, and used by this workspace's expand/Find Path interactions and by
  `GET /api/graph` callers generally — but the *initial* request for this specific workspace opts
  out of the backend's own `overview` default via `tier=all`. A future true bounded-fetch consumer
  (once chat grounding and search are reworked to tolerate a partial client-side graph) is left
  for a follow-up; seeing this tradeoff decided differently is the main open question below.

## Functional Requirements

### Domain modeling (backend)

- FR-1: `domain` is a new `profile_kind` (`src/cerebro/semantic/profile.py`), analogous to
  `entity`/`policy`/etc — id prefix `domain.`, backed by a new `DomainCandidate` model
  (`src/cerebro/models.py`: `id`, `name`, `description`, `classification`, optional `owner`,
  `warnings`).
- FR-2: An entity may declare `cerebro.domain: domain.<id>` (optional, not required — see Edge
  Cases). When present, `entity.links` must include that domain id (existing
  `semantic_links_mismatch` convention), and the id must resolve to a real `domain` object
  (`validate_profile_object` in `src/cerebro/semantic/validator.py`).
- FR-3: `src/cerebro/semantic/linker.py::profile_edges` emits a `domain_membership` edge
  (entity → domain, label "belongs to") whenever `cerebro.domain` is present, alongside the
  existing `entity_mapping` edge — reusing the exact pattern every other kind's edges follow.
- FR-4: The golden bundle (`knowledge/bank-workshop`) ships four authored domains —
  `domain.retail-banking`, `domain.lending`, `domain.cards`, `domain.operations` — and all 11
  entity files are backfilled with their domain.

### Progressive-disclosure graph API (backend)

- FR-5: `SemanticRetriever.graph(tier=None, node_id=None, depth=1)` (`src/cerebro/retrieval.py`)
  gains three optional parameters. `node_id` (+`depth`) takes precedence and returns that node's
  BFS neighborhood via the existing `expand()` method (the same adjacency MCP's
  `expand_neighborhood` and chat grounding already use). Otherwise `tier="overview"` restricts
  the result to `{"domain", "entity"}` nodes; `tier="all"`/`None` returns everything.
- FR-6: A new `src/cerebro/graph_projection.py::project_graph(graph, allowed_ids)` factors the
  "keep allowed nodes, then keep edges whose endpoints both survived" logic that both the new
  tier/node_id filtering and the pre-existing `customer_scope.filter_graph_for_customer_scope`
  now share (refactor, not a behavior change for the customer path).
- FR-7: `GET /api/graph` (`src/cerebro/api.py`) accepts `tier` (`overview`|`all`, 422 on anything
  else), `node_id`, and `depth` (`0`–`settings.graph_max_expand_depth`, default 3). With no
  `tier`/`node_id` given, the endpoint defaults to `settings.graph_default_tier` (`"overview"` by
  default, configurable via `CEREBRO_GRAPH_DEFAULT_TIER`). `scope=customer` composes on top:
  the tier/depth filter is applied first (over the full adjacency), then the customer allowlist
  clips the result — so a customer-scoped expansion still finds the right neighbors before being
  restricted.
- FR-8: A new `SemanticRetriever.path(from_id, to_id)` does a parent-pointer BFS over the same
  adjacency to find the shortest node-id route between two objects, or `None`. A new
  `GET /api/graph/path?from=&to=&scope=` endpoint returns `{"path": [...ordered ids...], nodes,
  edges}`, 404 if either id is unknown, composing with `scope=customer` the same way.
- FR-9: `CUSTOMER_SCOPE_OBJECT_IDS` (`src/cerebro/customer_scope.py`) gains
  `CUSTOMER_SCOPE_DOMAIN_IDS` (`domain.retail-banking`, `domain.lending`, `domain.cards` —
  `domain.operations` stays excluded, matching the entities it groups).

### Frontend rendering and interaction

- FR-10: The default type-filter state (`apps/web/src/App.tsx`) is `{domain, entity}` instead of
  every kind — this is the concrete "default view shows only domain/entity" enforcement point.
  "All"/other layer presets and individual kind checkboxes remain an explicit, one-click opt-in
  to see more, reusing 100% of the pre-existing filter/search/navigator machinery.
- FR-11: `apps/web/src/useGraphExplorer.ts` tracks `expandedIds` (nodes the user explicitly
  expanded, resolved to their 1-hop neighborhood via a client-side adjacency map built from the
  already-fetched graph's edges), `focusMode`, and a resolved `pathIds` (client-side BFS mirroring
  FR-8, for the common case where both endpoints are already graph-resident). `expandedIds` is
  unioned into the visible-id set alongside the type/search filter.
- FR-12: `GraphView.tsx` hides edges by default (`EDGE_HIDDEN_OPACITY`, a faint residual rather
  than fully invisible, so the canvas still reads as a connected map) and reveals them at full
  opacity on hover, selection/focus pathway, expansion, or an active Find Path (`.path-highlighted`
  class) — composing with the existing per-edge-type color/style rules.
- FR-13: Zoom-based LOD (`cy.on('zoom', ...)`): below `LOD_RELATIONSHIP_ZOOM`, audit-only
  `relationship`-kind nodes disappear entirely (extending spec 006 FR-517's "visually
  subordinate" treatment); below `LOD_LABEL_ZOOM`, non-focused/hovered/path node labels are
  suppressed to cut overview clutter.
- FR-14: Focus Mode (`focusMode` prop) turns the existing dim-on-select behavior into a true
  isolate: the selected/expanded pathway is forced visible and everything else is hidden
  (`display:none`), re-fit to the pathway; the non-focus-mode default stays dim-only.
- FR-15: Double-click/double-tap a node (`cy.on('dbltap', ...)`) calls `onExpand`, revealing its
  1-hop neighborhood regardless of the current type filter.
- FR-16: A two-click "Find Path" flow (`App.tsx`: arm via toolbar button, click a start node, click
  a destination node) resolves and highlights the route, with a canvas hint ("Find path: click the
  starting node…" / "…now click the destination node…").
- FR-17: A collapsible "Relationship types" filter facet (`GraphEdgeType`-keyed checkboxes,
  default all checked) hides edges of unchecked types regardless of interaction state — composing
  with, not replacing, FR-12's hide-by-default/reveal-on-interaction behavior.
- FR-18: `Inspector.tsx`'s table column list gains inline search once a table has more than 8
  columns, so the "Table → Column" tier stays navigable for wide real-world tables.
- FR-19: Node sets above `WORKER_LAYOUT_NODE_THRESHOLD` (300) run layout in
  `apps/web/src/graphLayout.worker.ts` (a `d3-force` simulation run to convergence, no DOM
  dependency) instead of synchronously on the main thread; smaller sets keep a synchronous
  `cytoscape-fcose` layout (replacing the previous bare `cose`) with a "Computing layout…" overlay
  shown while the worker resolves.
- FR-20: A new `domain` presentation entry (`apps/web/src/profilePresentation.ts`: gold `#E0B34D`,
  `star` shape, new `Domain` layer) and matching `domain_membership` edge style are added,
  extending every shared presentation/filter/navigator consumer automatically.

### Scale verification

- FR-21: `tests/fixtures/generate_large_bundle.py` programmatically generates a synthetic,
  fully-valid OKF bundle (domains + entities + physical tables only — the kinds the overview tier
  and 1-hop entity expansion actually touch) at a scale (1,000+ objects) the real golden bundle
  cannot exercise, round-tripping through the unmodified `BundleLoader`/`BundleValidator`.

## Acceptance Criteria

- AC-1: `GET /api/graph` with no params returns only `domain`/`entity` nodes
  (`test_retrieval_api_mcp.py::test_http_contracts_and_typed_errors`,
  `test_graph_overview_tier_returns_only_domain_and_entity_nodes`).
- AC-2: `GET /api/graph?tier=all` returns every object; `GET /api/graph?tier=unknown` is 422.
- AC-3: `GET /api/graph?node_id=X&depth=1` returns exactly `X`'s 1-hop neighborhood, matching
  `SemanticRetriever.expand([X], depth=1)` (`test_graph_node_id_depth_expansion_reuses_adjacency_bfs`).
- AC-4: `GET /api/graph/path?from=A&to=B` returns an ordered node-id route whose first/last ids are
  `A`/`B`; an unreachable or unknown id yields `{"path": []}` or 404 respectively
  (`test_retriever_path_finds_shortest_route_and_handles_unreachable_or_unknown_ids`).
- AC-5: `GET /api/graph?scope=customer&tier=overview` returns only the customer-visible domains
  (`retail-banking`, `lending`, `cards` — never `operations`) and entities
  (`test_api_graph_endpoint_customer_scope_composes_with_overview_tier`).
- AC-6: An entity with no declared `domain` still validates (`test_bundle_validation.py::
  test_entity_without_a_declared_domain_still_validates`); an entity with an unresolvable domain
  reference fails validation with `invalid_entity_domain`.
- AC-7: A synthetic 1,600+/3,600+-object bundle's `graph(tier="overview")` response stays exactly
  `domain_count + entity_count` nodes regardless of total bundle size, and a 1-hop expansion off a
  synthetic entity returns exactly 3 nodes (itself, its domain, its table), not something
  proportional to the total bundle (`tests/test_graph_scale.py`).
- AC-8: Default frontend load shows only domain/entity nodes and excludes every other kind
  (`App.test.tsx::filters canonical profile kinds...`, live-verified: initial load showed
  "14 / 72 objects", exactly 4 domains + 10 entities).
- AC-9: Selecting a node and enabling Focus Mode hides everything outside its neighborhood and
  reveals that neighborhood's edge labels (live-verified via screenshot).
- AC-10: Find Path between two domain nodes on opposite sides of the graph highlights the correct
  connecting route with labeled edges and dims everything else (live-verified via screenshot: a
  Cards → Card → Customer → Loan → Lending path).
- AC-11: The Customer graph tab composes the domain tier with the customer allowlist — default
  view is 10/44 objects, 3 domains (no Operations) — live-verified via screenshot.
- AC-12: The embedded "How your data connects" panel in the Customer self-service workspace still
  shows the full customer-scoped object set (not just domain/entity), since it has no filter UI of
  its own (live-verified via screenshot).

## Edge Cases

- EC-1: An entity with no declared domain does not fail validation and simply has no
  `domain_membership` edge — it falls into no domain grouping rather than an "unassigned" bucket
  node, since no such bucket entity exists in this iteration (open question below).
- EC-2: A domain kind not yet added to `PROFILE_KINDS` would silently normalize to `"generic"`
  (`normalize_profile_kind`'s existing fallback) rather than erroring — covered by this spec
  landing `"domain"` in `PROFILE_KINDS` from the start, and by the golden-bundle object-count
  assertions that would catch a regression.
- EC-3: Expanding an already-expanded node is idempotent (`expandedSeeds` is a `Set`, `expand`
  just re-adds the same id).
- EC-4: Find Path with no connecting path resolves to `null`/`{"path": []}`, not an error
  (covered by both the backend `path()` unit test and the frontend hook's unit test).
- EC-5: Collapsing one expanded node does not hide a neighbor still needed by another
  still-expanded node — `expandedIds` is recomputed fresh from the current `expandedSeeds` set on
  every change, not accumulated (covered by `useGraphExplorer.test.ts`).

## Interfaces / Contracts

- `GET /api/graph?scope=&tier=&node_id=&depth=` — `tier` and `scope` are independently validated
  (422 `unknown_tier`/`unknown_scope`); `node_id` takes precedence over `tier` when both are
  absent-vs-present is ambiguous only in that `node_id` alone (no `tier`) still applies the
  server's `graph_default_tier`-independent expand path (FR-5).
- `GET /api/graph/path?from=&to=&scope=` — new endpoint, 404 `unknown_concept` for an unknown id,
  `{"path": [...], "version", "nodes", "edges"}` otherwise.
- `DomainCandidate` (`src/cerebro/models.py`) — `id`, `name`, `description`, `classification`,
  `owner?`, `warnings`.
- `GraphView` new props (`apps/web/src/GraphView.tsx`): `expandedIds?`, `focusMode?`, `pathIds?`,
  `onExpand?`, `visibleEdgeTypes?` — all optional, default to today's behavior when omitted.
- `getGraph`/`getCustomerGraph` (`apps/web/src/api.ts`) now take an optional
  `{tier?, nodeId?, depth?}` query object before the `AbortSignal` (a breaking positional-arg
  change to both call sites, updated everywhere they're called).

## Constraints

- The domain field on an entity is optional, not required, for this rollout (see Non-Goals) —
  making it mandatory would force a breaking migration of every existing entity in one shot.
- No new backend settings beyond `CEREBRO_GRAPH_DEFAULT_TIER` and
  `CEREBRO_GRAPH_MAX_EXPAND_DEPTH`; the depth clamp mirrors the existing `expand_neighborhood` MCP
  tool's 0–3 range for consistency.
- `cytoscape-fcose` and `d3-force` are the only new frontend runtime dependencies (plus
  `@types/d3-force` and `@types/cytoscape-fcose` as dev dependencies) — both are lightweight,
  DOM-independent, and `d3-force` in particular is required to run inside a Web Worker.

## Assumptions

- The 4-domain grouping for the golden bundle (retail-banking / lending / cards / operations) is a
  reasonable, demo-scale taxonomy, not a claim about how a real bank would organize its domains.
- `WORKER_LAYOUT_NODE_THRESHOLD = 300` and the LOD zoom thresholds (`0.55`/`0.7`) are initial,
  reasonable defaults, not tuned against real user testing at "thousands of tables" scale (no such
  real bundle exists yet to tune against).

## Open Questions

- Should entities with no declared domain get a synthetic "Unassigned" domain node so the overview
  tier never has orphaned entities floating with no domain edge, once real production bundles have
  partially-migrated content? Deferred — the golden bundle ships fully tiered, so this has no
  effect today.
- Should the primary Semantic Constellation workspace's *initial* fetch become genuinely
  bounded (`tier=overview`) once chat-grounding highlighting and full-kind search are reworked to
  tolerate an incomplete client-side graph (e.g., by fetching additional kinds on demand)? Left for
  a follow-up; see the corresponding Non-Goal.
- Live double-click-to-expand was verified via unit tests (`useGraphExplorer.test.ts`) and code
  review, but not via browser automation — the automation tool's synthetic double-click did not
  reliably reach Cytoscape's internal `dbltap` gesture recognizer (a known category of issue with
  canvas-rendered libraries and synthetic events), and the force layout's continuous position
  settling made coordinate-based re-clicks racy. A human should manually double-click a node in a
  real browser session to close this verification gap.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-2601 | FR-1–FR-4 | `tests/test_bundle_validation.py` domain fixtures: valid domain, entity→valid/invalid domain, optional-domain-still-valid, `invalid_domain_contract`, `invalid_profile_id`; golden bundle object-count assertions include `domain: 4`. |
| T-2602 | FR-3 | `tests/test_retrieval_api_mcp.py::test_graph_edges_are_canonical_and_directional` asserts the `domain_membership` edge tuple. |
| T-2603 | FR-5, FR-6 | `test_graph_overview_tier_returns_only_domain_and_entity_nodes`, `test_graph_node_id_depth_expansion_reuses_adjacency_bfs`. |
| T-2604 | FR-7 | `test_http_contracts_and_typed_errors` (default tier, `tier=all`, unknown tier 422, node_id+depth expansion, path endpoint), `test_customer_scope.py::test_api_graph_endpoint_customer_scope_composes_with_overview_tier`. |
| T-2605 | FR-8 | `test_retriever_path_finds_shortest_route_and_handles_unreachable_or_unknown_ids`; HTTP-level path assertions in `test_http_contracts_and_typed_errors`. |
| T-2606 | FR-9 | `test_customer_scope.py` domain-inclusion assertions. |
| T-2607 | FR-10, FR-17 | `App.test.tsx::filters canonical profile kinds with presets, checkboxes, search, and reset` (default tier, edge-type checkbox toggle + reset). |
| T-2608 | FR-11, EC-3, EC-4, EC-5 | `useGraphExplorer.test.ts` (7 cases: initial state, expand/collapse, path resolution, unreachable/self path, clearPath, graph-version reset). |
| T-2609 | FR-12–FR-16, FR-20 | `GraphView.test.tsx` (presentation map incl. `domain`, edge grammar incl. `domain_membership`); live-verified via screenshots: default tier render, Focus Mode isolate with revealed edge labels, Find Path route highlight. |
| T-2610 | FR-18 | `Inspector.test.tsx` (no search box under 8 columns; search box appears and narrows/empties above 8). |
| T-2611 | FR-19 | `graphLayout.worker.test.ts` (3 cases: distinct finite positions, unknown-edge-id tolerance, empty graph); `WORKER_LAYOUT_NODE_THRESHOLD` exported for inspection. |
| T-2612 | FR-21, AC-7 | `tests/test_graph_scale.py` (4 cases: loader/validator round-trip, overview-tier boundedness, 1-hop expansion boundedness at 3,600-object scale, `tier=all` remains an explicit opt-in). |
