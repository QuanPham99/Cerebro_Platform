# Cerebro Semantic Layer Scaling Plan

> **Target:** Scale the current prototype from 10 tables to 200–300+ tables
>
> **Approach:** Preserve the OKF, Python, FastAPI, MCP, and React architecture while adding domain partitioning, incremental processing, durable artifacts, and bounded retrieval
>
> **Status:** Development plan; implementation requires feature specifications and test designs under `specs/`

## 1. Goal and success criteria

The goal is to support a lakehouse catalog containing at least 300 tables without sending source rows to an AI provider, rebuilding all semantics after every schema change, or loading the complete knowledge graph into every request and browser session.

The first production-scale milestone targets:

- 300 tables and up to 10,000 columns across 6–15 business domains.
- Up to 2,000 OKF semantic objects and 1,000 approved relationships.
- Incremental regeneration when a table, declaration, or source document changes.
- Reusable reviewed semantics for unchanged objects.
- Credential-free startup from checked-in or prebuilt semantic artifacts.
- Domain-aware HTTP and MCP retrieval with complete provenance.
- A graph UI that renders bounded neighborhoods instead of the full catalog.
- No source-row sampling, persistence, logging, or transmission to an AI provider.

The physical size of the lakehouse, including a lakehouse of 10,000 TB, is not a direct scaling factor for Cerebro. Discovery must read catalogs, schemas, manifests, lineage, and existing statistics rather than scanning table rows. Runtime cost should scale with the number of metadata objects and the number of changed objects.

## 2. Current baseline and scaling constraints

The current prototype provides a sound vertical slice but assumes one small active bundle:

| Area | Current behavior | Constraint at 200–300+ tables |
| --- | --- | --- |
| Discovery | `DuckDBSource.scan()` creates one complete in-memory snapshot | Exact expected counts treat normal drift as failure; there is no snapshot history or diff |
| Enrichment | The complete snapshot is sent through two structured model calls | Prompt size, provider latency, retries, and cost grow with the entire catalog |
| Generation | `cerebro generate` prints one proposal | Proposals are not durable, resumable, reviewable, or publishable |
| OKF bundle | One directory is recursively loaded and validated | No domain ownership, active-version registry, or targeted validation |
| Retrieval | All documents and optional embeddings are built in memory at startup | Startup can call the embedding provider for every object; lexical ranking scans all objects |
| API and MCP | One `SemanticRetriever` serves one active bundle | No domain routing, federated results, or version selection |
| Graph UI | `/api/graph` returns the complete graph | A full-catalog constellation becomes visually unusable and expensive to lay out |
| Evaluation | Ten questions cover one banking bundle | No domain-level, cross-domain, drift, or scale benchmark |

At the target size, Cerebro should remain a modular monolith. A distributed queue, external graph database, and separate vector service are not required until measurements show that the bounded in-process design cannot meet the acceptance budgets in this plan.

## 3. Target architecture

```text
Lakehouse catalogs and declared context
                 |
                 v
       Metadata-only source adapters
                 |
                 v
       Immutable catalog snapshots
                 |
                 v
        Object-level change detector
                 |
        +--------+---------+
        |                  |
        v                  v
  Unchanged objects   Changed-object jobs
  reuse reviewed OKF  bounded AI proposals
        |                  |
        +--------+---------+
                 v
       Deterministic validation
                 |
                 v
       Risk-based human review
                 |
                 v
  Immutable versioned domain bundles
                 |
                 v
       Prebuilt retrieval artifacts
                 |
                 v
  Domain router + lexical/vector/graph retrieval
                 |
          +------+------+
          v             v
      HTTP/MCP       Bounded graph UI
```

### Domain partitioning

Split the catalog into business-owned domains such as `customers`, `accounts`, `payments`, `cards`, `loans`, `risk`, and `operations`. A domain should normally contain 20–50 related tables. Domain membership is declared and reviewed; automatic clustering may suggest membership but must not silently change ownership.

Each domain owns:

- Its physical tables and column metadata.
- Business concepts and aliases.
- Query semantics, metrics, policies, and approved internal relationships.
- An immutable semantic version and independent review state.
- A prebuilt retrieval artifact.

A global registry owns:

- Active version per domain.
- Domain name, summary, aliases, owner, and classification ceiling.
- Approved cross-domain relationships.
- Source snapshot and index versions.

The existing `knowledge/bank-workshop` bundle remains valid as the default single-domain bundle. Multi-domain behavior must be additive and backward compatible.

### Unit of work

The pipeline must not use one model call for the entire catalog. It will use three bounded work units:

1. **Table unit:** one table plus its columns, declared keys, comments, lineage, and existing reviewed semantics.
2. **Relationship cluster:** up to 10 connected tables selected from declared keys, lineage, and approved join candidates.
3. **Domain synthesis:** summaries of reviewed table and relationship outputs, not the original full schemas.

Default AI concurrency is four work units, configurable up to eight. Each unit is idempotent, content-addressed, and retried at most twice. A failed unit is recorded for repair and must not invalidate unchanged reviewed semantics.

## 4. Development phases

### Phase 0 — Specifications and scale benchmark

Create an approved scaling specification before production changes, following `AI-rule.md`.

Deliverables:

- `specs/008-scalable-semantic-catalog.md` with functional requirements, contracts, edge cases, and test traceability.
- A deterministic synthetic catalog fixture containing 300 tables, approximately 10,000 columns, domain assignments, valid and invalid relationships, sensitive fields, and controlled schema changes.
- A benchmark command that records scan, load, validation, indexing, search, grounding, graph response, memory, and artifact-size measurements.
- A baseline report for the unchanged implementation to identify actual bottlenecks.

Exit criteria:

- The scale fixture can be generated reproducibly without external credentials.
- Current correctness and performance are measured rather than estimated.
- Later phases have requirement IDs and failing or pending tests before implementation.

### Phase 1 — Multi-domain configuration, snapshots, and change detection

Generalize the current source configuration without removing the existing single-file workflow.

Implementation changes:

- Introduce a source registry and domain manifests while continuing to accept `config/bank-source.yaml`.
- Replace exact table-count drift failure with a structured `CatalogDiff` containing added, removed, changed, and unchanged tables, columns, keys, and relationships.
- Persist immutable, canonical JSON snapshots identified by a SHA-256 content hash and source version.
- Calculate object fingerprints from physical metadata, declared context, and relevant source-document hashes.
- Produce an impact set containing directly changed objects and semantic dependents.
- Keep source adapters metadata-only and read-only; separate future profiling from catalog discovery.

Proposed CLI additions:

```text
cerebro scan --config <source.yaml> --snapshot-output <path>
cerebro diff --before <snapshot.json> --after <snapshot.json>
cerebro scan --registry <registry.yaml> --changed-only
```

Exit criteria:

- A one-column change in the 300-table fixture identifies only the changed table and its dependents.
- Reordered metadata produces the same snapshot hash.
- Removed tables and columns are reported as breaking changes rather than causing an untyped exception.
- No discovery path issues a query against source rows.

### Phase 2 — Incremental semantic enrichment

Replace whole-catalog enrichment with resumable table, relationship-cluster, and domain-synthesis jobs.

Implementation changes:

- Split the two current structured stages into bounded work-unit schemas while retaining the provider-neutral `GenerationProvider` interface.
- Give every proposal an ID, input fingerprint, domain, object IDs, provider/model metadata, prompt-template version, timestamp, confidence, and provenance.
- Store proposals as immutable artifacts so interrupted runs can resume without repeating completed calls.
- Reuse `human_reviewed` semantics whenever the corresponding input fingerprint is unchanged.
- Limit relationship candidates to discovered or declared keys, lineage, same-domain identifier matches, approved prior relationships, and top-k semantic candidates. Never compare every table pair.
- Reject model output that references objects outside the supplied work unit unless the reference is an approved registry object.
- Add token, call, latency, failure, and estimated-cost budgets per run; stop cleanly when a budget is reached.
- Keep credential-free fallback at the domain level so one unavailable provider does not disable the complete catalog.

Proposed CLI behavior:

```text
cerebro generate --domain payments --changed-only
cerebro generate --registry <registry.yaml> --resume <run-id>
cerebro generate --dry-run
```

`--dry-run` reports selected work units, reuse decisions, estimated calls, and affected domains without calling a provider or writing proposals.

Exit criteria:

- An unchanged second run performs zero provider calls.
- A change to one table invokes only its table unit, affected relationship clusters, and domain synthesis.
- Provider timeout, refusal, and malformed output leave a resumable run with typed failure records.
- Prompts never contain source rows, credentials, or unrelated domains.

### Phase 3 — Composition, validation, review, and publication

Complete the missing path between `SemanticProposal` and an active reviewed bundle.

Implementation changes:

- Add a deterministic composer that converts validated proposals and reused reviewed objects into OKF documents.
- Validate individual objects first, then domain links, then cross-domain links; cache successful validation by content hash.
- Introduce lifecycle states `draft`, `validated`, `review_required`, `approved`, `rejected`, `published`, and `deprecated` in the publication workflow without weakening existing OKF object status validation.
- Create a review queue ordered by risk: restricted data, new metrics, cross-domain mappings, many-to-many joins, fan-out warnings, conflicts, and low-confidence proposals first.
- Allow bulk approval only for low-risk proposals that pass deterministic validation; AI-generated business meaning is never auto-published.
- Publish an immutable domain version and atomically update the domain's active pointer only after all blocking issues are resolved.
- Roll back by moving the active pointer to an earlier immutable version; do not rewrite historical bundles.

Proposed CLI additions:

```text
cerebro compose --run <run-id>
cerebro validate --domain <domain> --version <version>
cerebro publish --domain <domain> --version <version>
cerebro rollback --domain <domain> --to-version <version>
```

Exit criteria:

- Invalid joins, dangling links, unresolved metric dependencies, and policy violations cannot be published.
- A failed publication leaves the prior active domain version unchanged.
- Every published object traces to a snapshot, declared source, AI proposal when applicable, validation report, and human decision.
- Existing `cerebro validate --bundle knowledge/bank-workshop` remains supported.

### Phase 4 — Prebuilt federated retrieval

Keep the current lexical, vector, reciprocal-rank-fusion, and typed-graph behavior while moving expensive work out of API startup.

Implementation changes:

- Add an `IndexBuilder` that produces one versioned retrieval artifact per domain.
- Cache embeddings by object content hash, embedding model, and normalization version; never request embeddings during normal API startup.
- Store semantic documents, cached vectors, graph edges, and index metadata in a separate writable DuckDB index artifact. Do not mix it with the read-only source database.
- Add a lightweight domain router that ranks domain summaries before object retrieval.
- Search only the top matching domains, then fuse lexical and vector rankings and perform bounded typed-graph expansion.
- Apply status, trust, classification, and policy filters before graph expansion enters the grounding response.
- Enforce request budgets for selected domains, candidate objects, graph depth, response objects, and response bytes.
- Load the existing single bundle directly when no registry or prebuilt index is configured.

Default retrieval limits:

- Search the top three domains unless the caller provides a domain filter.
- Return at most 20 ranked seeds.
- Expand one graph hop by default and at most three when explicitly requested.
- Return at most 100 semantic objects in one grounding package.

API and MCP compatibility:

- Preserve `GET /api/search`, `POST /api/grounding`, and all three existing MCP tool names.
- Add optional `domains`, `semantic_versions`, and `max_objects` inputs.
- Add selected domain/version evidence to responses while preserving all existing response fields.
- Reject unavailable versions and unauthorized classification requests with typed errors.

Exit criteria:

- API startup makes zero network calls and does not rebuild embeddings.
- Updating one domain rebuilds only that domain's retrieval artifact.
- The same query, registry version, and index version return deterministic rankings.
- Domain routing does not prevent required golden objects from appearing within the configured top-k results.

### Phase 5 — Bounded graph and review UI

Change the UI from a full-catalog constellation to progressive domain and neighborhood exploration.

Implementation changes:

- Load domain summaries and counts first; do not fetch every node during application startup.
- Add domain selection, server-side search, pagination, and classification/status filters.
- Fetch a bounded subgraph around search results or selected objects.
- Cap a rendered Cytoscape view at 200 nodes and show an explicit refinement message when a result exceeds the cap.
- Lazy-load object details and OKF source documents.
- Virtualize long table, search-result, and review-queue lists.
- Preserve the current single-bundle full graph for small catalogs behind the same UI contract.
- Add review queue views for conflicts, sensitive classifications, ambiguous joins, and changed semantics.

Proposed endpoints:

```text
GET /api/domains
GET /api/domains/{domain_id}
GET /api/graph/neighborhood?ids=&depth=&limit=
GET /api/review/items?domain=&risk=&cursor=
```

Exit criteria:

- Initial UI load does not download the complete 300-table graph.
- Search-to-neighborhood navigation works with keyboard and screen-reader semantics.
- No graph response exceeds its node, edge, or byte limit.
- Domain, object, and active-version provenance remains visible in the inspector.

### Phase 6 — Evaluation, observability, and release hardening

Expand the current golden evaluation into a release gate for correctness, scale, and recovery.

Implementation changes:

- Maintain at least ten domain questions per domain plus cross-domain questions for every approved cross-domain relationship.
- Add change-impact fixtures covering additions, removals, renames, type changes, key changes, sensitive classifications, and metric dependency changes.
- Record retrieval recall at 10, reciprocal rank, selected domains, policy exclusions, latency, and response size.
- Emit structured run summaries for scan, diff, generation, validation, publication, indexing, and retrieval.
- Add health checks for active domain versions, index compatibility, stale snapshots, failed work units, and unavailable provider fallback.
- Exercise interrupted generation, partial domain publication, index corruption, provider outage, and rollback.

Reference performance gate:

Run the credential-free 300-table fixture on a recorded 4-vCPU/8-GB environment.

| Operation | Acceptance budget |
| --- | --- |
| API startup with prebuilt indexes | Under 10 seconds and zero provider calls |
| Cached bundle and index memory | Under 1.5 GB |
| `GET /api/search` p95 | Under 300 ms |
| `POST /api/grounding` p95 | Under 750 ms |
| Bounded graph response p95 | Under 500 ms for at most 200 nodes |
| Golden retrieval | 100% of required IDs within top 10 for blocking questions |
| Incremental regeneration | Zero calls for unchanged objects; only impact-set calls after a controlled change |

Performance budgets may be revised only with benchmark evidence and an updated specification; they must not be silently weakened to pass a release.

Exit criteria:

- All functional, contract, security, recovery, and scale tests pass.
- A clean credential-free deployment starts from published artifacts.
- One controlled schema change completes scan, diff, targeted enrichment, review, publish, and reindex without regenerating unrelated domains.
- The release report states exact checks, measurements, skipped provider tests, and remaining risks.

## 5. Interfaces and data contracts

The implementation specifications should introduce these minimum records:

| Record | Required fields |
| --- | --- |
| `CatalogSnapshot` extension | `snapshot_id`, `captured_at`, `source_id`, canonical object fingerprints |
| `CatalogDiff` | before/after IDs, added, removed, changed, unchanged, breaking changes |
| `DomainManifest` | stable ID, name, aliases, owners, source/table selectors, active version |
| `EnrichmentWorkUnit` | run ID, unit ID/type, domain, input fingerprint, object IDs, dependencies |
| `SemanticProposal` extension | proposal ID, prompt version, confidence, input/output hashes, review state |
| `PublishedDomain` | domain ID, semantic version, snapshot IDs, bundle hash, index version |
| `RetrievalIndexManifest` | domain/version, object count, embedding model, content hashes, build time |

Stable object IDs must not depend on display names or bundle versions. Renames require an explicit alias or supersession record so existing metrics, links, and client references can be migrated safely.

## 6. Test strategy

Every phase must add requirement-linked tests before it is considered complete.

| Test level | Required coverage |
| --- | --- |
| Unit | Canonical hashing, diff classification, impact traversal, work-unit selection, validation, domain routing, limits |
| Contract | Snapshot, proposal, registry, OKF, index-manifest, HTTP, MCP, and backward-compatible response schemas |
| Integration | Scan-to-snapshot, changed-only generation with mock provider, compose/validate/publish, index build, federated retrieval |
| Scale | 300-table fixture, 10,000 columns, dense and disconnected graphs, bounded UI/API responses |
| Recovery | Interrupted generation, malformed proposal, stale index, failed publication, rollback, provider outage |
| Security | No row access, path containment, classification filtering, prompt redaction, bounded request inputs |
| Regression | Existing 10-table golden bundle, ten golden questions, HTTP/MCP tools, and React graph behavior |

Provider-dependent tests must use deterministic mocks in the default suite. A separately reported live-provider smoke test may verify integration, but lack of credentials must not block the release gate.

## 7. Delivery sequence and ownership

Suggested delivery is six implementation sprints after Phase 0:

1. **Foundation:** registry, canonical snapshots, structured diffs, and scale fixture.
2. **Incremental generation:** bounded work units, artifact persistence, resume, and budgets.
3. **Governance:** composition, layered validation, review queue, publication, and rollback.
4. **Retrieval:** prebuilt domain indexes, domain router, bounded grounding, and compatibility tests.
5. **Experience:** progressive graph UI, server-side search, lazy detail, and review views.
6. **Hardening:** scale gates, observability, recovery exercises, documentation, and release report.

Each sprint must leave the existing single-bundle demo operational. New multi-domain behavior should be feature-configured until its regression, scale, and rollback gates pass.

## 8. Explicit non-goals for the 300-table milestone

- Scanning or profiling raw lakehouse rows.
- Supporting 10,000 tables in the first scaling release.
- Introducing a distributed workflow engine, external graph database, or managed vector service without benchmark evidence.
- Automatically approving AI-generated business meaning.
- Autonomous cross-domain ownership changes.
- SQL generation, SQL execution, or result validation.
- Authentication and multi-tenancy beyond enforcing the classification hooks required by current contracts.
- Real-time semantic regeneration; scheduled and event-triggered incremental runs are sufficient.

## 9. Final release definition

The scaling milestone is complete when Cerebro can load the deterministic 300-table fixture, reuse unchanged reviewed semantics, enrich only a controlled impact set, publish versioned domain bundles, start without provider calls, retrieve required grounding across domains within the performance budgets, and display bounded graph neighborhoods without exposing source rows or weakening provenance and review requirements.
