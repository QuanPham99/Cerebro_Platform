# 006 — Knowledge Graph UI

## Problem

Reviewers need to understand how physical banking data maps to business meaning, metrics, relationships, and policy without reading every Markdown file.

## Goal

Deliver a read-only Obsidian-inspired graph explorer backed by the active OKF bundle.

## Non-Goals

- Editing or approving semantic definitions.
- Rendering individual columns as graph nodes.
- Recreating Obsidian branding or plugin APIs.

## Functional Requirements

- FR-501: Display a dark three-pane layout: discovery/filter rail, graph canvas, and semantic inspector.
- FR-502: Render dataset, table, concept, relationship, metric, and policy nodes with redundant shape/color encoding.
- FR-503: Render typed physical FK, semantic mapping, metric dependency, and policy coverage edges.
- FR-504: Selecting a node highlights its one-hop semantic pathway, labels related edges, fades unrelated topology, and updates the inspector.
- FR-505: Support search, type filters, reset, fit, zoom, pan, hover, keyboard selection, and internal OKF links.
- FR-506: Show columns in the inspector rather than as nodes.
- FR-507: Provide loading, empty, and error states; respect reduced motion; collapse inspector to a drawer on small screens.

## Acceptance Criteria

- AC-501: The reference bundle is visible and navigable without editing controls.
- AC-502: Search and filters change the visible graph predictably.
- AC-503: Inspector exposes definition, grain, fields, joins, metrics, warnings, policy, and provenance where present.
- AC-504: Keyboard users can focus and select every visible node.
- AC-505: Production build succeeds and responsive layouts do not overflow.

## Edge Cases

- No matching search results, long labels, disconnected nodes, API failure, and thousands of columns.

## Interfaces / Contracts

Consumes `/api/graph`, `/api/search`, and `/api/concepts/{id}`. Graph DTO contains `nodes[]` and typed `edges[]`.

## Constraints

React, TypeScript, Vite, and Cytoscape. Palette: night `#0B1020`, surface `#151D30`, text `#E7ECF4`, muted `#8491A7`, data cyan `#58C7D9`, meaning violet `#A78BFA`.

## Assumptions

Desktop demo is primary; mobile remains functional.

## Open Questions

Graph authoring and saved views are future work.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-501 | FR-501–FR-503 | Component test renders layout, node legend, and typed edges. |
| T-502 | FR-504–FR-506 | Interaction tests selection, search, filtering, inspector. |
| T-503 | FR-507 | Tests loading/error/empty and reduced-motion class. |
| T-504 | AC-505 | Type-check and production Vite build. |
