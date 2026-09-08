# 006 — Knowledge Graph UI

## Problem

Reviewers need to understand how physical banking data maps to business meaning, metrics, relationships, and policy without reading every Markdown file.

## Goal

Deliver a read-only, profile-aware graph explorer backed by active and candidate OKF bundles.

## Non-Goals

- Editing or approving semantic definitions.
- Rendering individual columns as graph nodes.
- Recreating Obsidian branding or plugin APIs.

## Functional Requirements

- FR-501: Display a dark three-pane layout: discovery/filter rail, graph canvas, and semantic inspector.
- FR-502: Preserve raw OKF `type`, but use backend `profile_kind` as the canonical presentation, filtering, navigation, and inspection key for dataset, physical table, entity, dimension, metric, business rule, relationship, policy, legacy concept, and generic nodes.
- FR-503: Use one shared presentation contract across filters, navigator, graph, and inspector: dataset/Physical/`#58C7D9`/round-rectangle; physical table/Physical/`#3EA6B8`/rectangle; entity/Semantic/`#A78BFA`/ellipse; dimension/Semantic/`#60A5FA`/barrel; business rule/Semantic/`#5CCB8A`/octagon; metric/Metrics/`#F2B56B`/hexagon; relationship/Semantic/`#6E7A90`/diamond; policy/Governance/`#F17B91`/tag; legacy concept/Semantic/`#8B79C6`/ellipse; generic/Other/`#8993A8`/ellipse.
- FR-504: Selecting a node highlights its one-hop semantic pathway, labels related edges, fades unrelated topology, and updates the inspector.
- FR-505: Support search, type filters, reset, fit, zoom, pan, hover, keyboard selection, and internal OKF links.
- FR-506: Show columns in the inspector rather than as nodes.
- FR-507: Provide loading, empty, and error states; respect reduced motion; collapse inspector to a drawer on small screens.
- FR-508: Group visible keyboard-navigator objects into collapsible sections by semantic object type.
- FR-509: Label every navigator section with its plural object type, matching color/shape marker, and visible-object count.
- FR-510: Preserve selection, search/filter results, keyboard operation, and the no-results state within the grouped navigator.
- FR-511: Render every physical FK edge with persistent source and target endpoint labels derived from its declared cardinality (`one-to-one`, `one-to-many`, `many-to-one`, or `many-to-many`).
- FR-512: Point the physical FK edge arrow from its declared source table toward its declared target table; endpoint labels, rather than the arrow alone, identify the one/many sides.
- FR-513: Explain the "many → one" notation in the edge legend and identify it as a table join.
- FR-514: Do not add cardinality endpoint labels to semantic mappings, relationship endpoints, metric dependencies, or policy coverage edges.
- FR-515: Provide layer presets for All, Physical, Semantic, Metrics, and Governance while retaining independent per-kind checkboxes; reset restores All and every kind.
- FR-516: Render every backend edge family explicitly: violet solid entity mappings; blue solid dimension ownership; blue dotted dimension bindings; amber solid metric ownership; amber dashed metric compatibility; amber dotted metric physical dependencies; green solid rule ownership; green dashed rule dependencies; slate directional semantic relationships with cardinality; teal directional physical joins with endpoint cardinalities; rose dotted policy coverage; and muted arrowless relationship membership.
- FR-517: Relationship audit nodes remain searchable, navigable, and inspectable, but are smaller and visually subordinate to canonical entity-to-entity semantic relationship edges.
- FR-518: Group the keyboard navigator by the same shared profile presentation metadata used by filters and graph styling.
- FR-519: Branch the inspector on `profile_kind`: entities expose physical table/key/grain/aliases; dimensions expose owner/bindings/type/metrics; metrics expose owner, aggregation or ratio, fields, filters, grain, dimensions, time anchor, dependencies, and warnings; rules expose owner/output/grain/dependencies/constraints/classifications; relationships expose semantic and physical endpoints plus validation evidence; tables expose schema/grain/keys/fields/classifications; policies expose rule/scope/confidence/evidence/warnings. Every kind exposes normalized status, sources, generation metadata, verification, provenance, and source document.
- FR-520: Accept legacy `active` status at the frontend boundary and present it as `stable`.

## Acceptance Criteria

- AC-501: The reference bundle is visible and navigable without editing controls.
- AC-502: Search and filters change the visible graph predictably.
- AC-503: Inspector exposes every applicable profile contract plus common governance metadata without leaking raw unsupported values.
- AC-504: Keyboard users can focus and select every visible node.
- AC-505: Production build succeeds and responsive layouts do not overflow.
- AC-506: Teal table nodes, including Accounts and Branches when visible, appear under a section titled "Tables."
- AC-507: Each non-empty object type appears in its own native collapsible section; empty type sections are omitted.
- AC-508: Selecting an object from a grouped section still selects that object and updates the inspector.
- AC-509: The Account Branch edge reads Accounts "many" → "one" Branches regardless of their positions in the generated layout.
- AC-510: Physical FK cardinality is visible without first selecting a related node, and each supported cardinality maps to the correct source and target labels.
- AC-511: Semantic and relationship-object edges retain their existing notation and do not display "many" or "one" endpoint labels.
- AC-512: Every profile kind participates in fixtures, search, filtering, layer presets, navigator grouping, candidate preview, reset, and focused-path behavior.
- AC-513: Every edge selector has an asserted direction, color, line style, cardinality-label policy, and arrowless membership behavior.

## Edge Cases

- No matching search results, long labels, disconnected nodes, API failure, thousands of columns, and all four supported physical cardinalities.

## Interfaces / Contracts

Consumes `/api/graph`, `/api/search`, and `/api/concepts/{id}`. Graph nodes contain raw `type` plus canonical `profile_kind`; edge types are `physical_fk`, `relationship_endpoint`, `semantic_mapping`, `entity_mapping`, `dimension_entity`, `dimension_binding`, `metric_entity`, `metric_dimension`, `metric_dependency`, `rule_entity`, `rule_dependency`, `semantic_relationship`, and `policy_coverage`.

## Constraints

React, TypeScript, Vite, and Cytoscape. Palette: night `#0B1020`, surface `#151D30`, text `#E7ECF4`, muted `#8491A7`, data cyan `#58C7D9`, meaning violet `#A78BFA`.

## Assumptions

Desktop demo is primary; mobile remains functional.

## Open Questions

Graph authoring and saved views are future work.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-501 | FR-501–FR-503 | Component test renders all profile kinds from the shared presentation map and every typed edge. |
| T-502 | FR-504–FR-506 | Interaction tests selection, search, filtering, inspector. |
| T-503 | FR-507 | Tests loading/error/empty and reduced-motion class. |
| T-504 | AC-505 | Type-check and production Vite build. |
| T-505 | FR-508–FR-509 / AC-506–AC-507 | Component test verifies grouped titles, markers, counts, and table membership. |
| T-506 | FR-510 / AC-508 | Component test verifies omitted empty groups, no-results guidance, and selection callback. |
| T-507 | FR-508–FR-510 | Web test suite and production build provide regression and static verification. |
| T-508 | FR-511–FR-512 / AC-509–AC-510 | Unit test verifies all supported cardinality values map to the correct persistent source/target labels and that physical edges have a target arrow. |
| T-509 | FR-513–FR-514 / AC-511 | Component test verifies explanatory, table-scoped legend copy and the physical-only style selector. |
| T-510 | FR-511–FR-514 | Web test suite and production build provide regression and static verification. |
| T-511 | FR-515, FR-518 | Test every layer preset, per-kind checkbox, search result, navigator group, candidate preview, and reset transition. |
| T-512 | FR-516–FR-517 | Assert every Cytoscape edge selector's arrows, line style, color, labels, and relationship-node subordinate styling. |
| T-513 | FR-504, AC-512 | Focus each representative node and assert connected semantic paths stay visible while unrelated topology fades. |
| T-514 | FR-519–FR-520 | Render entity, dimension, metric, rule, relationship, table, and policy inspectors including common metadata and active-to-stable normalization. |
