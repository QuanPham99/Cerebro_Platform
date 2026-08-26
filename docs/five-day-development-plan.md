# Cerebro Five-Day Development Plan

## Objective

Deliver one polished vertical slice:

```text
Mock PostgreSQL bank schema
    -> generate OKF
    -> review and edit
    -> publish
    -> explore the graph
    -> retrieve Text-to-SQL grounding through MCP
```

The implementation is optimized for a reliable hackathon demonstration. It preserves OKF compatibility and stable integration contracts while deferring production infrastructure.

## Day 1 — Scan and OKF foundation

- Scaffold FastAPI, React, PostgreSQL/pgvector, and Docker Compose.
- Pin and reuse the necessary parser and validator code from Google's maintained [Open Knowledge Format repository](https://github.com/GoogleCloudPlatform/open-knowledge-format).
- Scan one PostgreSQL schema for tables, columns, comments, primary keys, and foreign keys.
- Store one metadata snapshot and convert it into draft OKF v0.2 documents.
- Support only the supplied retail-banking mock schema.

### End-of-day result

A repeatable command can scan the mock PostgreSQL schema and produce structurally valid draft OKF documents.

## Day 2 — AI enrichment and publishing

- Use the OpenAI Responses API with structured outputs and a configurable model, defaulting to `gpt-5.4-mini`.
- Run one bounded enrichment workflow that proposes:
  - Business descriptions and aliases.
  - Table purpose and grain.
  - Approved joins derived from foreign keys.
  - Basic PII and financial-data classifications.
- Store typed SQL-grounding metadata under a namespaced `cerebro` frontmatter block.
- Validate all physical references and relationships before publishing.
- Publish an immutable timestamped bundle and update an active-version pointer.
- Defer automated Git commits until after the hackathon prototype.

### End-of-day result

The mock bank schema can be converted into enriched, validated OKF proposals and published as an immutable active bundle.

## Day 3 — Retrieval and MCP

- Index approved concepts using PostgreSQL full-text search and `text-embedding-3-small` embeddings.
- Combine keyword and vector rankings, then include directly connected tables and approved relationships.
- Expose three MCP tools over local Streamable HTTP:
  - `retrieve_grounding(question, limit)`
  - `get_concept(concept_id)`
  - `expand_neighborhood(concept_ids, depth=1)`
- Include concept IDs, descriptions, columns, joins, classifications, provenance, confidence, and active bundle version in tool responses.
- Apply one fixed demonstration policy that excludes concepts classified as restricted.
- Check in versioned MCP schemas and example requests and responses for the Text-to-SQL team.

### End-of-day result

The Text-to-SQL application can retrieve a complete grounding package from the published semantic layer through MCP.

## Day 4 — Review and graph UI

- Build a dark, Obsidian-inspired knowledge graph using React and Cytoscape.
- Distinguish tables, business concepts, columns, and metrics through node styling.
- Distinguish physical joins and semantic links through edge styling.
- Focus the one-hop neighborhood when a node is selected and dim unrelated nodes.
- Open a docked inspector containing the selected concept, evidence, provenance, and validation state.
- Let reviewers edit descriptions, aliases, classifications, and join guidance through structured form controls.
- Provide approve, reject, validate, and publish actions.
- Defer canvas-based edge creation, graph history, alternate layouts, and schema-change overlays.

### End-of-day result

A reviewer can explore the semantic graph, correct an AI proposal, approve it, and publish the updated bundle without editing files manually.

## Day 5 — Integration and demo hardening

- Connect the Text-to-SQL team's agent to the MCP service using the versioned contract and fixtures.
- Create 8–10 curated retail-banking questions with expected concepts, tables, joins, and classifications.
- Measure retrieval results and correct failures that affect the demonstration set.
- Add a repeatable seed/reset command.
- Verify startup from a clean Docker Compose environment.
- Rehearse the complete demonstration and keep one pre-published fallback bundle for use if AI generation is unavailable.

### End-of-day result

The complete workflow starts cleanly and can be demonstrated from PostgreSQL onboarding through grounded Text-to-SQL retrieval in less than five minutes.

## MCP interface contract

### `retrieve_grounding`

Input:

```json
{
  "question": "Which customers had the largest outgoing transaction volume?",
  "limit": 10
}
```

Required output fields:

- Active semantic version.
- Ranked business concepts and physical tables.
- Required columns and approved join path.
- Relevant filters and grain.
- Sensitivity classifications and warnings.
- Provenance and review state.
- Retrieval confidence or ranking evidence.

### `get_concept`

Returns one complete OKF concept, including standard frontmatter, Markdown body, and its `cerebro` extension.

### `expand_neighborhood`

Returns the typed nodes and edges within one hop of the supplied concept IDs. The prototype fixes the maximum traversal depth at one.

## Acceptance tests

- PostgreSQL metadata generates valid OKF documents with correct primary-key and foreign-key relationships.
- Invalid or broken concepts cannot be published.
- An AI proposal cannot become active without validation and reviewer approval.
- A reviewer can edit and approve a generated concept through the web interface.
- Publishing switches retrieval to the newly approved bundle.
- Graph selection highlights the correct neighborhood and relationship types.
- Each golden question retrieves its required tables and join path within the top ten results.
- Every MCP response includes the active semantic version and provenance.
- Restricted concepts are excluded by the fixed demonstration policy.
- The complete demonstration starts from a clean environment and finishes within five minutes.

## Explicitly deferred

- Schema-change detection and targeted regeneration.
- Document uploads, web crawling, data profiling, and sample-value analysis.
- Authentication, multi-user workflows, role management, and real banking data.
- Durable job queues, distributed services, deployment automation, and production observability.
- Automated Git publication and pull-request workflows.
- Reports, dashboards, SQL generation, and query execution.
- Sources and SQL dialects other than PostgreSQL.

## Review checklist

Before implementation begins, confirm that:

- The mock bank schema contains sufficient primary and foreign keys to demonstrate meaningful join paths.
- The Text-to-SQL team agrees to the three MCP tool schemas.
- The expected concepts and joins for the 8–10 golden questions are documented.
- An OpenAI API key and the selected model are available for the demonstration environment.
- A pre-published fallback OKF bundle is included in the repository.
