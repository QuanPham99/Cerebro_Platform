# 010 — Database-only generation, review, and activation

## Goal

Generate an OKF candidate from a DuckDB catalog without importing configured or checked-in knowledge, then require an explicit human decision before a separately verified activation updates every runtime consumer.

## Architecture

`DuckDB catalog -> sanitized CatalogSnapshot -> structural semantic agents -> deterministic compiler + validator -> whole-candidate review -> immutable reviewed bundle -> explicit activation -> user-authored metric/rule revision -> review -> activation`

The source adapter owns database access. The model stages receive only the sanitized snapshot. The compiler and validator own structural correctness. A self-declared reviewer owns business acceptance. No multi-agent framework is required. A future documentation-enrichment extension may add approved external evidence, but it is disabled and absent in database-only runs.

## Functional requirements

- FR-901: Generation accepts `configured` and `database_only`; omission remains `configured` for API compatibility while the Build UI always uses `database_only`.
- FR-902: `database_only` requires only the server database path and schema, never loads source YAML, checked-in OKF, URLs, documents, rules, declared joins, descriptions, classifications, or aliases.
- FR-903: Discovery uses DuckDB catalog functions only and reads table/column names, types, nullability, native comments, and supported single-column PK/FK constraints. It never selects source rows.
- FR-904: Database-only source name is the database filename stem and source version is a stable fingerprint of catalog facts.
- FR-905: Persist and expose a sanitized `snapshot.json` with no local database path and evidence including `config_loaded: false`, `web_enrichment: disabled`, `row_sampling: disabled`, and `rows_read: 0`.
- FR-906: The smoke orchestrator makes bounded typed calls for inventory and relationship semantics. It never calls query-semantics generation; model inputs contain only sanitized catalog facts, database facts remain `discovered`, and generated structural semantics remain `ai_proposed`.
- FR-907: Candidate inspection exposes objects, relationship endpoints/cardinality/confidence/evidence, metric formulas, classifications, warnings, provenance, and generated Markdown.
- FR-908: Review applies to the complete candidate. Approval requires a non-empty reviewer and explicit AI-risk acknowledgement. Rejection additionally requires a comment.
- FR-909: A review decision revalidates and hashes the candidate. Approval atomically copies it to `knowledge/reviewed/<run-id>`, writes `approval.json`, and sets manifest `review_state: approved` without rewriting object provenance.
- FR-910: Identical repeated decisions are idempotent. A different later decision returns a typed conflict.
- FR-911: Activation is separate from review. It accepts approved reviewed bundles only, verifies the recorded digest, atomically updates the active pointer, and swaps HTTP, graph, document, chat, and MCP runtime state.
- FR-912: Rejected, changed, invalid, unfinished, or unapproved candidates cannot activate.
- FR-913: HTTP provides snapshot, document, review, and activation endpoints; CLI provides equivalent source-mode, review, and activation operations.
- FR-914: Database-only business semantics may propose policies from table and column names only. Such policies remain `ai_proposed`, include exact catalog-name evidence and confidence, and never rewrite discovered classifications.
- FR-915: Candidate compilation rejects orphaned or invented concept mappings, metric dependencies, and policy targets instead of silently dropping them. Validation failures are exposed as sanitized progress/run errors and cannot reach review or activation.
- FR-916: Metrics and business rules can be authored only from an approved active graph, either through a compact typed form or an LLM translation of natural language using approved graph metadata only.
- FR-917: Adding a definition clones the active bundle into a new `authored` candidate revision. It never edits the active, reviewed, or golden bundle in place and still requires review plus separate activation.
- FR-918: Definition validation resolves entities, dimensions, table dependencies, physical columns, metric measure trees, and rule dependencies before the draft graph is exposed. Metric compatibility backlinks are derived only inside the draft revision.
- FR-919: `bank-workshop` v0.2.0 is a read-only golden graph and post-generation oracle. Structural smoke evaluation excludes the intentionally deferred metric and business-rule kinds.

## HTTP and CLI contracts

- `POST /api/generation/runs` body: `{ "source_mode": "configured" | "database_only" }`.
- `GET /api/generation/runs/{id}/snapshot` returns sanitized discovery evidence and facts.
- `GET /api/generation/runs/{id}/trace` returns the current ordered, sanitized stage inputs and outputs for the local server-process run.
- `GET /api/generation/runs/{id}/documents/{path}` returns candidate Markdown under the candidate root only.
- `POST /api/generation/runs/{id}/reviews` accepts `{decision, reviewer, comment, acknowledge_ai_risk}`.
- `POST /api/generation/runs/{id}/activate` has no review side effect.
- `GET /api/bundles/golden`, `GET /api/golden/graph`, and `GET /api/golden/objects/{id}` expose the permanent comparison graph.
- `GET /api/definitions/context` exposes approved graph metadata for the composer; `POST /api/definitions/translate` returns one typed definition proposal.
- `POST /api/definition-revisions` creates an authored draft; revision graph/object, add-definition, review, and activation routes preserve the same governance boundary as generated candidates.
- `cerebro generate --source-mode configured|database-only` (hyphenated CLI spelling maps to `database_only`).
- `cerebro review --bundle PATH --reviewer NAME --decision approve|reject [--comment TEXT] --acknowledge-ai-risk`.
- `cerebro activate --bundle PATH`.

## Test matrix

| ID | Requirement | Verification |
|---|---|---|
| T-901 | Catalog-only database access | Instrument `execute`; allow only `duckdb_tables()`, `duckdb_columns()`, and `duckdb_constraints()` and reject table-row SQL. |
| T-902 | Raw workshop discovery | Assert 10 tables, 75 columns, zero native relationships/comments, `rows_read: 0`, and no config load. |
| T-903 | Sealed inputs | Capture all provider prompts; assert no database path, row sentinel, configured descriptions/joins/rules, checked-in OKF, URLs, or web content. |
| T-904 | Bounded compilation | Reject invented endpoints and invalid formulas/links; preserve discovered versus AI-proposed provenance. |
| T-905 | Snapshot/doc inspection | Assert sanitized snapshot and path-contained Markdown access; reject traversal and non-Markdown files. |
| T-906 | Review validation | Reject missing reviewer/risk acknowledgement and rejection without comment. |
| T-907 | Decision durability | Assert receipt persistence, immutable approved copy, idempotent repeats, and typed conflicting-decision response. |
| T-908 | Digest gate | Modify reviewed content after approval and assert activation fails. |
| T-909 | Activation gate | Block unfinished, invalid, rejected, candidate, and unapproved bundles. |
| T-910 | Runtime swap | After activation, assert active bundle, HTTP graph/concepts/documents/chat version, and MCP tools resolve the reviewed version. |
| T-911 | Build workflow | Require source-mode selection and expose discovery evidence, inspection, review, rejection/approval, and separate activation. |
| T-912 | Oracle isolation | Compare generated joins to checked-in bank semantics only after generation; report precision/recall plus missing/invented relationships. |
| T-913 | AI policy boundary | Assert a catalog-name-derived policy retains `ai_proposed` provenance, targets real tables, records evidence/confidence, and does not change discovered column classifications. |
| T-914 | Semantic target gate | Reject empty, missing, non-table, duplicate, or mismatched semantic targets with typed errors and block review/activation. |
| T-915 | Deferred definitions | Assert smoke mode skips the query agent, then create a typed metric/rule revision only from an approved active graph. |
| T-916 | Immutable golden | Hash golden v0.2.0 before and after definition authoring and assert only the derived draft changes. |
| T-917 | Definition governance | Validate draft graph projection, persisted review state, immutable reviewed copy, digest gate, and separate activation. |

## Boundaries

The initial implementation is local and single-user. Reviewer identity is self-declared. Editing is limited to new metric and business-rule definitions in an authored revision; arbitrary object editing, authentication, signatures, uploads, non-DuckDB sources, concurrent agent swarms, row sampling, and web enrichment are out of scope.
