# 003 — Semantic Enrichment

## Problem

Catalog metadata identifies structures but not business meaning, analytical grain, safe joins, or banking-specific query guidance.

## Goal

Produce structured semantic proposals in three bounded stages, with a provider-neutral interface and deterministic fallback.

## Non-Goals

- Unbounded or autonomous agent loops.
- Autonomous publication of unvalidated output.
- Sending source rows or credentials to a model.

## Functional Requirements

- FR-201: Define a provider-neutral structured-generation protocol.
- FR-202: Provide an OpenAI Responses adapter defaulting to `gpt-5.4-mini`.
- FR-203: Stage one proposes table purpose plus typed business concepts and policies. Every emitted concept maps to at least one catalog table; every emitted policy applies to at least one catalog table and records a rule, confidence, and catalog-name evidence.
- FR-204: Stage two proposes relationships with typed endpoints, cardinality, confidence, and evidence; stage three proposes grain, dimensions, typed measures, time rules, and fan-out warnings. Every emitted measure has a formula and at least one catalog-table dependency.
- FR-205: Model input is limited to catalog snapshot, declared manifest, and schema reference text.
- FR-206: Validate all three stages with Pydantic before composition. Reject empty targets inside emitted concepts, metrics, or policies while allowing an entire category to be empty when the catalog does not support it.
- FR-207: If credentials or provider execution are unavailable, activate the checked-in golden bundle and report `generation_mode: fallback`.
- FR-208: Expose generation through `cerebro generate`.

## Acceptance Criteria

- AC-201: A mocked provider completes all three stages using structured JSON.
- AC-202: Invalid or incomplete model output, including an orphan concept, metric, or policy, is rejected before OKF publication.
- AC-203: Prompts contain no row samples.
- AC-204: Credential-free generation succeeds through fallback without changing the golden bundle.

## Edge Cases

- Provider timeout, refusal, malformed JSON, or schema mismatch.
- A proposed join or semantic target references an undeclared object.
- Duplicate aliases map ambiguously.

## Interfaces / Contracts

`GenerationProvider.generate(schema_name, prompt, output_model)` and `SemanticEnricher.enrich(snapshot) -> SemanticProposal`. Specification 008 adds candidate compilation and activation.

## Constraints

Temperature and model metadata are recorded; proposals have `ai_proposed` provenance until reviewed.

## Assumptions

The schema reference is trusted as declared context, not discovered truth.

## Open Questions

Human review workflow is deferred beyond the read-only prototype.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-201 | FR-201–FR-204 | Mock provider captures three typed calls and returns connected concept, metric, and policy proposals. |
| T-202 | FR-205 | Assert prompt inputs contain metadata but no sample values. |
| T-203 | FR-206 | Malformed and orphaned fixtures raise validation errors; empty whole categories remain valid. |
| T-204 | FR-207, FR-208 | Credential-free CLI selects fallback and exits successfully. |
