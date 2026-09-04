# 005 — Semantic Retrieval and MCP

## Problem

A text-to-SQL agent needs a small, explainable semantic grounding packet rather than the entire schema or an opaque vector result.

## Goal

Retrieve relevant OKF objects using lexical/vector fusion and graph expansion, then expose them through HTTP and MCP.

## Non-Goals

- SQL generation or execution within this retrieval subsystem; the separately gated consumer is specified in 008.
- Persistent vector infrastructure.
- Conversation memory or autonomous planning within the retriever; bounded client history and orchestration are specified in 008.

## Functional Requirements

- FR-401: Index title, ID, aliases, tags, descriptions, columns, and body in memory.
- FR-402: Rank lexically and, when configured, embed with `text-embedding-3-small`; fuse rankings by reciprocal rank fusion with `k=60`.
- FR-403: Expand one hop over typed edges and apply trust, status, policy, and classification filters.
- FR-404: Fall back to lexical plus graph retrieval when embeddings are unavailable.
- FR-405: Serve active bundle, graph, concept detail, and search HTTP endpoints.
- FR-406: Expose MCP tools `retrieve_grounding`, `get_concept`, and `expand_neighborhood` over local Streamable HTTP.
- FR-407: Grounding includes semantic version, mode, concepts, tables, columns, joins, grain, metrics, filters, warnings, classifications, provenance, and ranking evidence.
- FR-408: The graph emits one canonical directional edge for each semantic contract: concept maps to table, metric depends on table, policy applies to table, dataset contains table, and physical relationships point from source table to target table. Reverse table back-references and duplicate generic relationship edges are omitted.
- FR-409: The graph viewer distinguishes concept, metric, policy, physical, and relationship-endpoint edges by source-aligned color, line pattern, arrow presence, and a direction-explicit legend. Human-readable edge labels appear on hover, tap, or focused neighborhoods.

## Acceptance Criteria

- AC-401: Retrieval is deterministic for the same bundle and query.
- AC-402: All ten golden questions return required concepts and joins within top 10.
- AC-403: Missing embeddings never prevent server startup.
- AC-404: MCP tool responses validate against the same schemas as HTTP responses.
- AC-405: Unknown IDs and malformed parameters return typed errors.
- AC-406: Active and candidate graph responses contain the same canonical edge directions and no reverse duplicates.
- AC-407: Concept, metric, policy, and physical edges have visible target arrows; relationship endpoint edges remain arrowless.

## Edge Cases

- Empty query, disconnected graph, duplicate/reverse edges, zero vector norm, and restricted concepts.

## Interfaces / Contracts

HTTP: `GET /api/bundles/active`, `/api/graph`, `/api/concepts/{id}`, `/api/search?q=&types=`. MCP tools use JSON-compatible typed inputs/outputs.

## Constraints

No external database; active bundle is parsed at startup.

## Assumptions

Local callers can reach the MCP Streamable HTTP route without authentication during the demo.

## Open Questions

Production identity and tenant isolation are deferred.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-401 | FR-401–FR-404 | Unit tests lexical, fusion, graph expansion, and fallback. |
| T-402 | FR-405 | API tests success, filtering, and typed errors. |
| T-403 | FR-406, FR-407 | MCP client invokes all three tools and validates output. |
| T-404 | AC-402 | Golden-question evaluation asserts required top-10 IDs. |
| T-405 | FR-408, AC-406 | Assert exact source, target, type, and label for every canonical edge family and absence of reverse duplicates. |
| T-406 | FR-409, AC-407 | Assert typed Cytoscape styles, focused/hovered labels, and direction-explicit legend content. |
