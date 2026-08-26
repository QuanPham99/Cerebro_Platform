# Semantic Layer Prototype — Implementation Compliance

Date: 2026-08-26  
Branch: `feature/okf-semantic-layer-sdd`  
Overall status: **COMPLIANT**

## Delivered scope

The five-day prototype implements catalog-only DuckDB discovery, two-stage structured semantic enrichment with credential-free fallback, a checked-in and validated OKF banking bundle, explainable in-memory retrieval, REST and Streamable HTTP MCP interfaces, and a read-only semantic graph UI.

## Compliance matrix

| ID | Status | Evidence |
|---|---|---|
| C-01 Pinned baseline | COMPLIANT | Full Google OKF subtree at `vendor/open-knowledge-format`; bundle records upstream URL and commit `ad30107c31c06aec8a7d5636e0d1058118604e6f`; upstream parser smoke-tested. |
| C-02 Read-only/no rows | COMPLIANT | `DuckDBSource` always connects with `read_only=True`, queries only `information_schema`, marks sampling disabled, and raises `SamplingDisabledError`; enrichment prompt tests exclude row examples. |
| C-03 Complete discovery | COMPLIANT | Live reference scan returns exactly 10 tables and 75 columns, with 10 declared primary keys and 11 declared relationships. |
| C-04 Provenance | COMPLIANT | Catalog columns carry `discovered`; manifest keys/joins/rules carry `declared`; generation schemas support `ai_proposed`; golden objects record `human_reviewed` or derived sources. |
| C-05 Structured enrichment | COMPLIANT | Provider-neutral `GenerationProvider`, OpenAI Responses adapter (`gpt-5.4-mini`), two Pydantic-validated stages, and checked-in fallback; mock-provider tests cover both stages. |
| C-06 Valid OKF | COMPLIANT | 36 semantic objects parse through Google's `OKFDocument` and pass Cerebro validation; tests reject duplicates/dangling links, missing endpoints, undeclared columns, invalid cardinality, dependencies, filters, and formulas. |
| C-07 Semantic mappings | COMPLIANT | Nine business concepts map physical structures to business meaning; four reviewed metrics and banking rules encode grain, safe joins, transaction direction, time anchoring, fan-out, and policy warnings. |
| C-08 Hybrid retrieval | COMPLIANT | Lexical indexing, optional `text-embedding-3-small`, RRF `k=60`, typed one-hop expansion, status/type filtering, deterministic fallback, and both hybrid/fallback tests are implemented. |
| C-09 MCP contract | COMPLIANT | `retrieve_grounding`, `get_concept`, and `expand_neighborhood` are mounted at `/mcp/`; an official MCP client completed initialization, tool listing, and grounding invocation over Streamable HTTP. |
| C-10 Working graph UI | COMPLIANT | React/Cytoscape constellation renders all 36 objects with typed nodes/edges, filters, search, keyboard navigator, focus/fade path, inspector, source links, responsive layout, and reduced-motion handling; component tests and a desktop visual smoke check pass. |
| C-11 Golden evaluation | COMPLIANT | `evaluation/golden-questions.yaml` covers ten workshop questions; every case retrieves its required concepts, tables, metrics, and joins. |
| C-12 Reproducible startup | COMPLIANT | README documents install/build/scan/generate/validate/evaluate/serve; golden mode requires no provider credentials; production server returned health/UI responses and the MCP semantic version in an end-to-end smoke run. |

## Verification record

```text
PYTHONPATH=src pytest -q
17 passed

PYTHONPATH=src python3 -m cerebro validate
valid: true, document_count: 36

PYTHONPATH=src python3 -m cerebro evaluate
10 / 10 PASS

cd apps/web && npm test
2 / 2 PASS

cd apps/web && npm run build
TypeScript and Vite production build PASS
```

The runtime smoke test started the combined FastAPI/static UI service, received `200 OK` from health and UI routes, initialized an MCP client using protocol `2025-06-18`, listed all three tools, and invoked `retrieve_grounding` with semantic version `0.1.0` in `lexical_graph` fallback mode.

## Prototype boundaries

- Live AI output is validated but is not auto-published; the reviewed golden bundle remains active.
- Embeddings are held in memory and are rebuilt on startup when configured.
- Authentication, tenancy, durable indexing, human approval workflows, SQL generation, and SQL execution remain out of scope.
- The source database declares no PK/FK constraints, so all keys and relationships retain manifest provenance rather than being presented as discovered database facts.

## Final determination

**COMPLIANT** — all C-01 through C-12 requirements have implementation and verification evidence within the agreed five-day prototype boundary.
