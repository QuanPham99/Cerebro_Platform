# Cerebro Semantic Layer Specifications

Status: Approved implementation baseline  
Target: five-day hackathon prototype  
Branch: `feature/okf-semantic-layer-sdd`

These specifications are the contract for implementation. Production code must trace to a functional requirement and an acceptance test below.

## Delivery sequence

1. [001 — Google OKF baseline](001-google-okf-baseline.md)
2. [002 — Bank source discovery](002-bank-source-discovery.md)
3. [003 — Semantic enrichment](003-semantic-enrichment.md)
4. [004 — OKF contract validation](004-okf-contract-validation.md)
5. [005 — Semantic retrieval and MCP](005-semantic-retrieval-mcp.md)
6. [006 — Knowledge graph UI](006-knowledge-graph-ui.md)
7. [007 — End-to-end demo](007-end-to-end-demo.md)
8. [008 — OKF agents and governed database chat](008-okf-agents-and-governed-chat.md)
9. [009 — Generation observability UI](009-generation-observability-ui.md)
10. [010 — Database-only generation, review, and activation](010-database-only-review-activation.md)

## Shared constraints

- Google Open Knowledge Format (OKF) is vendored unmodified and pinned to commit `ad30107c31c06aec8a7d5636e0d1058118604e6f`.
- The source DuckDB is opened read-only. No source rows are sampled, persisted, logged, or sent to an AI provider.
- Discovered facts, declared facts, AI proposals, and reviewed facts retain distinct provenance.
- The checked-in golden bundle is the default demo input and makes the demo independent of credentials.
- Bounded semantic agents perform typed definition, relationship, and query-semantics stages; discovery, compilation, validation, and activation remain deterministic.
- Text-to-SQL execution is read-only, policy-gated, local DuckDB access as specified in 008.

## Compliance IDs

The final report evaluates: C-01 pinned baseline; C-02 read-only/no rows; C-03 complete discovery; C-04 provenance; C-05 structured enrichment; C-06 valid OKF; C-07 semantic mappings; C-08 hybrid retrieval; C-09 MCP contract; C-10 working graph UI; C-11 golden evaluation; C-12 reproducible startup.
