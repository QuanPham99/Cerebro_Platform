# 003 — Semantic Enrichment

## Problem

Catalog metadata identifies structures but not business meaning, analytical grain, safe joins, or banking-specific query guidance.

## Goal

Produce structured semantic proposals through three explicit, bounded semantic agents, with a provider-neutral interface, deterministic orchestration, and deterministic fallback.

## Non-Goals

- Unbounded or autonomous agent loops.
- Autonomous publication of unvalidated output.
- Sending source rows or credentials to a model.

## Functional Requirements

- FR-201: Define a provider-neutral structured-generation protocol.
- FR-202: Provide an OpenAI Responses adapter defaulting to `gpt-5.4-mini`.
- FR-203: `SemanticInventoryAgent.run(snapshot) -> BusinessSemantics` makes exactly one `business_semantics` provider call and proposes typed entities, dimensions, table purpose, and policies. Every entity and dimension binds only to catalog tables and columns; inventory dimensions leave future metric compatibility empty because metrics do not exist yet. Every policy applies to at least one catalog table and records a rule, confidence, and catalog-name evidence.
- FR-204: `RelationshipAgent.run(snapshot, inventory) -> RelationshipSemantics` makes exactly one `relationship_semantics` provider call and proposes physical and optional semantic relationship endpoints, cardinality, confidence, and evidence. `MetricRuleAgent.run(snapshot, inventory, relationships) -> QuerySemantics` makes exactly one `query_semantics` provider call and proposes structured metrics, business rules, time semantics, dimension compatibility, and aggregation constraints. The deterministic linker derives reverse dimension-to-metric compatibility only from returned structured metrics.
- FR-205: Model input is limited to catalog snapshot, declared manifest, and schema reference text.
- FR-206: Validate all three stages with Pydantic before composition. Reject empty, duplicate, unknown, or type-incompatible semantic targets while allowing an entire category to be empty when the catalog does not support it.
- FR-207: If credentials or provider execution are unavailable, generate a structural candidate and report `generation_mode: fallback`; activation still requires review.
- FR-208: Expose generation through `cerebro generate`.
- FR-209: Each agent builds its own catalog-only prompt, accepts its provider through constructor injection, returns an empty typed collection when catalog evidence cannot support a semantic category, and cannot compile documents, validate candidates, review, activate, or read source rows.
- FR-210: `SemanticEnricher` remains the deterministic facade: it sanitizes the snapshot once, executes the enabled bounded agents in order, emits compatible external stage IDs, and preserves structural fallback behavior.
- FR-211: Agent output envelopes reject unknown fields so incorrect provider keys cannot silently become empty categories. A successful empty metric/rule category remains valid; the linker omits unconfirmed inventory forward hints, records warnings, and compiles the remaining candidate.
- FR-212: The full-pipeline smoke-test mode executes `SemanticInventoryAgent -> RelationshipAgent`, does not call `MetricRuleAgent`, and emits `query_semantics` as a compatible skipped stage with reason `post_activation_authoring`. Metrics and rules remain empty without degrading an otherwise successful structural proposal.

## Acceptance Criteria

- AC-201: A mocked provider completes all three stages using structured JSON.
- AC-202: Invalid or incomplete model output, including an orphan concept, metric, or policy, is rejected before OKF publication.
- AC-203: Prompts contain no row samples.
- AC-204: Credential-free generation succeeds through fallback without changing the golden bundle.
- AC-205: A smoke run compiles and validates a candidate containing no metrics or business rules without dangling dimension compatibility references.

## Edge Cases

- Provider timeout, refusal, malformed JSON, or schema mismatch.
- A proposed join or semantic target references an undeclared object.
- Duplicate aliases map ambiguously.

## Interfaces / Contracts

`GenerationProvider.generate(schema_name, prompt, output_model)`, `SemanticInventoryAgent.run(snapshot) -> BusinessSemantics`, `RelationshipAgent.run(snapshot, inventory) -> RelationshipSemantics`, `MetricRuleAgent.run(snapshot, inventory, relationships) -> QuerySemantics`, and `SemanticEnricher.enrich(snapshot) -> SemanticProposal`. Specification 008 adds candidate compilation and activation.

The general builder can enable all three agents. The Build smoke path is:

`Catalog Scan -> SemanticInventoryAgent -> RelationshipAgent -> query_semantics (skipped) -> Semantic Linker -> OKF Compiler -> Validator -> Human Review -> Activation`.

## Constraints

Temperature and model metadata are recorded; proposals have `ai_proposed` provenance until reviewed.

## Assumptions

The schema reference is trusted as declared context, not discovered truth.

## Open Questions

Source-row relationship profiling and the future Text-to-SQL planning-agent topology are deferred.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-201 | FR-201–FR-204, FR-209 | Unit-test each agent for its typed output, one provider call, schema name, catalog-only payload, upstream context, and supported empty result. |
| T-202 | FR-205 | Assert prompt inputs contain metadata but no sample values. |
| T-203 | FR-206 | Malformed and orphaned fixtures raise validation errors; empty whole categories remain valid. |
| T-204 | FR-207, FR-208 | Credential-free CLI selects fallback and exits successfully. |
| T-205 | FR-210 | Assert exact three-call order, compatible stage IDs, per-stage agent metadata, and unchanged candidate/review/activation gates. |
| T-206 | FR-203, FR-204, FR-211 | Reject unknown envelope fields and compile an empty metric/rule result without dangling compatibility links. |
| T-207 | FR-212 | Assert smoke mode never calls the metric/rule provider, emits a skipped compatible stage, and produces a non-degraded structural proposal. |
