# Cerebro Semantic Layer Specifications

Status: Approved implementation baseline  
Target: five-day hackathon prototype  
Branch: `feature/upgrade_semantic_layer`

These specifications are the contract for implementation. Production code must trace to a functional requirement and an acceptance test below.

## Delivery sequence

### Phase 1 — semantic layer (delivered)

1. [001 — Google OKF baseline](001-google-okf-baseline.md)
2. [002 — Bank source discovery](002-bank-source-discovery.md)
3. [003 — Semantic enrichment](003-semantic-enrichment.md)
4. [004 — OKF contract validation](004-okf-contract-validation.md)
5. [005 — Semantic retrieval and MCP](005-semantic-retrieval-mcp.md)
6. [006 — Knowledge graph UI](006-knowledge-graph-ui.md)
7. [007 — End-to-end demo](007-end-to-end-demo.md)
8. [011 — Cerebro Semantic Profile v0.1](011-semantic-profile-v0.1.md)
9. [012 — OKF agents and governed database chat](012-okf-agents-and-governed-chat.md)
10. [013 — Generation observability UI](013-generation-observability-ui.md)
11. [014 — Database-only generation, review, and activation](014-database-only-review-activation.md)

### Phase 2 — grounded query agents

8. [008 — Text-to-SQL agent](008-text-to-sql-agent.md)
9. [009 — Insight and report agent](009-insight-report-agent.md)
10. [010 — Verified corpus and adaptation](010-verified-corpus-and-adaptation.md)

Phase 2 consumes the Phase 1 bundle as read-only input and does not modify it.

### Post-launch fixes

1. [015 — Chat grounding and SQL-validator precision fixes](015-chat-grounding-and-validator-precision-fixes.md)
2. [018 — Vietnamese preset questions with live-validated SQL guarantee](018-vietnamese-preset-questions.md)
3. [019 — Chat gateway diagnostics and correlated console logging](019-chat-gateway-diagnostics.md)

### Operations and deployment

1. [016 — Docker and GreenNode vServer deployment](016-docker-greennode-vserver-deployment.md)
2. [017 — GreenNode Agent Runtime deployment with baked-in database](017-greennode-agent-runtime-deployment.md)

## Phase 2 scope change

Phase 1 declared text-to-SQL execution out of scope. Phase 2 brings generation and read-only execution in, under these bounds:

- Query execution here is demo glue, not the Governed Query Executor described in the README. Identity, authorization, audit logging, and cost accounting remain out of scope.
- Generation runs against a hosted provider using an organizer-supplied key, read from the environment and never committed. Because inference is remote, prompt content is bounded by construction: no source row, no `restricted` value, and no non-aggregated `confidential` value is ever sent. This extends the boundary spec 003 set for enrichment.
- The test suite runs with no key and no network, replaying recorded provider responses.
- Every check that gates execution is deterministic and runs without a model.
- A governed metric formula is embedded verbatim, verified by AST comparison, never rewritten by a model.
- Reports may not contain a number absent from the query result or a derived fact.

## Shared constraints

- Google Open Knowledge Format (OKF) is vendored unmodified and pinned to commit `ad30107c31c06aec8a7d5636e0d1058118604e6f`.
- The source DuckDB is opened read-only. No source rows are sampled, persisted, logged, or sent to an AI provider.
- Discovered facts, declared facts, AI proposals, and reviewed facts retain distinct provenance.
- The checked-in golden bundle is the default demo input and makes the demo independent of credentials.
- Bounded semantic agents perform typed inventory, relationship, and metric/rule stages; discovery, linking, compilation, validation, review, and activation remain deterministic.
- Text-to-SQL execution is read-only, policy-gated, local DuckDB access as specified in 008.

## Compliance IDs

The final report evaluates: C-01 pinned baseline; C-02 read-only/no rows; C-03 complete discovery; C-04 provenance; C-05 structured enrichment; C-06 valid OKF; C-07 semantic mappings; C-08 hybrid retrieval; C-09 MCP contract; C-10 working graph UI; C-11 golden evaluation; C-12 reproducible startup.
