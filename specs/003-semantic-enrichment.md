# 003 — Semantic Enrichment

## Problem

Catalog metadata identifies structures but not business meaning, analytical grain, safe joins, or banking-specific query guidance.

## Goal

Produce structured semantic proposals in two bounded stages, with a provider-neutral interface and deterministic fallback.

## Non-Goals

- One agent per semantic layer.
- Autonomous publication of unvalidated output.
- Sending source rows or credentials to a model.

## Functional Requirements

- FR-201: Define a provider-neutral structured-generation protocol.
- FR-202: Provide an OpenAI Responses adapter defaulting to `gpt-5.4-mini`.
- FR-203: Stage one proposes table purpose, business concepts, definitions, aliases, and classifications.
- FR-204: Stage two proposes grain, dimensions, measures, joins, time rules, and fan-out warnings.
- FR-205: Model input is limited to catalog snapshot, declared manifest, and schema reference text.
- FR-206: Validate both stages with Pydantic before composition.
- FR-207: If credentials or provider execution are unavailable, activate the checked-in golden bundle and report `generation_mode: fallback`.
- FR-208: Expose generation through `cerebro generate`.

## Acceptance Criteria

- AC-201: A mocked provider completes both stages using structured JSON.
- AC-202: Invalid or incomplete model output is rejected before OKF publication.
- AC-203: Prompts contain no row samples.
- AC-204: Credential-free generation succeeds through fallback without changing the golden bundle.

## Edge Cases

- Provider timeout, refusal, malformed JSON, or schema mismatch.
- A proposed join references an undeclared object.
- Duplicate aliases map ambiguously.

## Interfaces / Contracts

`GenerationProvider.generate(schema_name, prompt, output_model)` and `SemanticEnricher.enrich(snapshot) -> SemanticProposal`.

## Constraints

Temperature and model metadata are recorded; proposals have `ai_proposed` provenance until reviewed.

## Assumptions

The schema reference is trusted as declared context, not discovered truth.

## Open Questions

Human review workflow is deferred beyond the read-only prototype.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-201 | FR-201–FR-204 | Mock provider captures two typed calls and returns a proposal. |
| T-202 | FR-205 | Assert prompt inputs contain metadata but no sample values. |
| T-203 | FR-206 | Malformed fixtures raise validation errors. |
| T-204 | FR-207, FR-208 | Credential-free CLI selects fallback and exits successfully. |
