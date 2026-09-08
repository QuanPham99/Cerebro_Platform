# 011 — Cerebro Semantic Profile v0.1

## Goal

Represent governed entities, dimensions, structured metrics, business rules, relationships, and policies as a strict Cerebro profile inside permissive OKF v0.2 documents, then let the existing bounded generation workflow propose that profile without reading source rows.

## Architecture

`Catalog Scan -> SemanticInventoryAgent -> RelationshipAgent -> Semantic Linker -> OKF Compiler -> Validator -> Human Review -> Activation -> Definition Composer`

The checked-in golden bundle defines the profile contract before agent output is migrated. OKF owns portable Markdown metadata; `cerebro.kind` selects a strict profile model.

## Functional requirements

- FR-1001: A bundle-root `index.md` may declare only `okf_version: "0.2"`; nested `index.md` files may contain no frontmatter. Reserved index and log files are not semantic objects.
- FR-1002: Load arbitrary non-empty OKF `type` values and preserve unknown fields. Normalize `name` to `title`, `active` to `stable`, and current concepts to `legacy_concept` without rewriting source files.
- FR-1003: Expose normalized profile kinds for datasets, physical tables, entities, dimensions, metrics, business rules, relationships, policies, legacy concepts, and generic OKF objects.
- FR-1004: Validate typed physical mappings, entity keys and grain, dimension bindings, aggregate or ratio metrics, rule dependencies, relationship endpoints/cardinality, and semantic links.
- FR-1005: The golden bundle contains the current physical catalog plus first-class entities, dimensions, metrics, rules, relationships, and policy documents. Physical table documents contain discovered physical facts rather than reverse semantic backlinks.
- FR-1006: Retrieval resolves semantic objects first, expands semantic relationships second, and adds the minimum physical bindings and join paths third.
- FR-1007: Grounding adds entities, dimensions, and rules without removing existing concepts, tables, metrics, joins, warnings, classifications, provenance, or ranking evidence.
- FR-1008: One deterministic orchestrator sanitizes the catalog snapshot once and invokes exactly three bounded provider-wrapper agents in order: semantic inventory, relationship semantics, and metric/rule semantics. Existing external progress-stage identifiers remain compatible and applicable events identify the responsible agent.
- FR-1009: Database-only prompts contain sanitized catalog facts only. They exclude database paths, source rows, configured declarations, checked-in semantics, URLs, and evaluation answers.
- FR-1010: The deterministic compiler rejects empty, duplicate, unknown, or type-incompatible semantic and physical references before a candidate becomes reviewable.
- FR-1011: Credential-free fallback emits only structural dataset, table, and catalog-relationship documents; it does not invent business semantics.
- FR-1012: Generated semantic documents remain `draft` and `ai_proposed`. Approval preserves provenance, adds human verification, promotes the reviewed copy to `stable`, and computes the approval digest after promotion.
- FR-1013: Relationship validation uses catalog constraints and declared metadata only. Source-row uniqueness, coverage, and fanout profiling remain disabled and are represented as `not_checked`.
- FR-1014: Semantic evaluation reports object-kind and join-path accuracy independently from SQL execution or result correctness. Golden semantics are post-generation oracle data only.
- FR-1015: Generated semantic declarations and references accept bare or correctly prefixed identifiers in snake case, kebab case, mixed case, or whitespace form and emit one lower-kebab canonical ID. Physical table and column identifiers preserve their catalog spelling.
- FR-1016: `MetricRuleAgent` owns metric-to-dimension compatibility. The linker derives reverse dimension compatibility from metrics that exist, reports unconfirmed inventory hints, and permits a valid candidate with empty metric and rule categories.
- FR-1017: The smoke profile intentionally contains no generated metrics or business rules. The stable query-semantics stage is skipped, and structural validation remains sufficient for review.
- FR-1018: After activation, a user may add a typed metric or business rule through natural-language translation or a compact form. The LLM may use approved graph metadata but no source rows.
- FR-1019: Authored definitions live in a derived candidate revision and reuse validation, immutable review, digest, and explicit activation gates.
- FR-1020: Every graph uses the same `profile_kind` presentation contract; metrics are amber/yellow hexagons and business rules are green octagons.

## Profile contracts

- `Entity`: one primary table, one or more key columns, a typed grain, aliases, classification, and warnings.
- `Dimension`: one owning entity, one or more physical column bindings, categorical, temporal, numeric, geographic, or derived semantic type, optional derivation, compatible metric IDs, classification, and warnings.
- `Metric`: one owning entity, aggregate or ratio measure, typed column bindings and predicates, grain, compatible dimensions, optional time dimension/anchor, classification, and warnings.
- `BusinessRule`: one owning entity, rule kind, output type, semantic dependencies, non-empty logic, grain, classification, and warnings.
- `Relationship`: optional semantic entity endpoints, required physical table/column endpoints, cardinality, default join type, evidence, confidence, and catalog-only validation state.

## Implemented architecture

The smoke builder implements two structural typed agents followed by deterministic linking, OKF compilation, profile validation, isolated candidate review, and explicit activation. The optional `MetricRuleAgent` contract remains available outside smoke mode for compatibility, but the UI smoke workflow does not invoke it. After activation, the definition composer adds user-owned metrics and rules through the same validation and governance gates. Agents receive catalog-only inputs and cannot read rows, compile documents, validate candidates, review, or activate. The graph is an implemented projection of entities, dimensions, metrics, business rules, policies, physical bindings, governed semantic relationships, and audit relationship objects.

## Future Text-to-SQL target

A later runtime may separate intent, semantic planning, physical planning, SQL generation, SQL validation, and answer composition into bounded agents. That topology is a target, not current behavior. It does not change this builder's HTTP, MCP, review, activation, or stage contracts.

## Compatibility and boundaries

- Existing concept-based bundles and candidate proposals continue to load and validate through legacy adapters.
- Existing HTTP routes, MCP tool names, review/activation requests, and progress-stage names remain stable.
- Query-planner decomposition, SQL-architecture migration, and source-row profiling remain outside this specification.

## Test design

| ID | Requirement | Verification |
|---|---|---|
| T-1001 | FR-1001–FR-1003 | Load v0.2 indexes, legacy objects, and an unknown OKF type; assert normalized metadata and kinds. |
| T-1002 | FR-1004, FR-1010 | Mutate each profile kind with missing or incompatible references and assert typed validation codes. |
| T-1003 | FR-1005 | Validate exact golden object counts, 10 tables, and 75 columns. |
| T-1004 | FR-1006, FR-1007 | Resolve representative metric, dimension, rule, entity, physical binding, and join paths through grounding. |
| T-1005 | FR-1008, FR-1009 | Unit-test the three agents, capture exactly one typed call each in exact orchestration order, assert upstream context and sealed database-only prompts, and verify compatible stages plus agent metadata. |
| T-1006 | FR-1010, FR-1011 | Reject hallucinated generated targets; assert credential-free output has no proposed business objects. |
| T-1007 | FR-1012 | Approve a draft candidate and assert stable reviewed documents, human verification, and digest protection. |
| T-1008 | FR-1013 | Instrument DuckDB access and permit catalog queries only; assert `rows_read: 0`. |
| T-1009 | FR-1014 | Run semantic and legacy evaluation independently and compare generated output only after generation. |
| T-1010 | FR-1015, FR-1016 | Compile underscore and prefixed semantic IDs to the same canonical links; compile empty metric/rule output without dangling dimension compatibility while retaining strict required-reference failures. |
| T-1011 | FR-1017–FR-1019 | Skip query semantics in smoke mode, create an authored metric/rule revision from an approved graph, and preserve review/activation gates. |
| T-1012 | FR-1020 | Assert canonical profile colors and shapes are identical for golden, generated, candidate, and authored graphs. |
