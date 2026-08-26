# 004 — OKF Contract and Validation

## Problem

Generic Markdown alone cannot guarantee that semantic joins, metric dependencies, policies, and provenance form a usable grounding contract.

## Goal

Publish a Google-compatible OKF bundle with a small validated Cerebro extension and reject unsafe or broken semantics.

## Non-Goals

- Replacing upstream OKF syntax.
- Executing generated SQL.
- Supporting every semantic modeling language.

## Functional Requirements

- FR-301: Publish `knowledge/bank-workshop` with index, datasets, tables, concepts, relationships, metrics, and policies folders.
- FR-302: Every document contains valid upstream OKF frontmatter and stable identifiers.
- FR-303: Validate Cerebro fields for grain, mappings, cardinality, join columns, metric formulas/filters, classification, warnings, and provenance.
- FR-304: Reject dangling links, missing endpoints, undeclared join columns, invalid cardinality, and unresolved metric dependencies.
- FR-305: Include transaction volume, card-fraud rate, late-payment rate, and non-performing-loan rate metrics.
- FR-306: Encode positive transaction amounts, inflow/outflow categories, distinct transaction grains, MAX-date anchoring, and restricted synthetic PII policies.
- FR-307: Expose validation through `cerebro validate`.

## Acceptance Criteria

- AC-301: Golden bundle contains all 10 tables, 11 physical relationships, required concepts/rules, and four metrics.
- AC-302: Upstream and Cerebro validators pass the golden bundle.
- AC-303: One invalid fixture exists for every rejection class in FR-304.
- AC-304: Internal OKF links resolve relative to the bundle root.

## Edge Cases

- Duplicate IDs, cycles, absent optional descriptions, and unsupported file extensions.
- Metric denominator may be zero; contract must declare safe division.

## Interfaces / Contracts

Markdown plus YAML frontmatter. `BundleLoader.load(path) -> SemanticBundle`; `BundleValidator.validate(bundle) -> ValidationReport`.

## Constraints

Extensions live under the `cerebro` namespace where possible; documents remain readable without Cerebro.

## Assumptions

Declared relationships are authoritative because DuckDB has no PK/FK constraints.

## Open Questions

Formal upstream extension registration is outside the prototype.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-301 | FR-301, FR-302 | Load every Markdown document with upstream parser. |
| T-302 | FR-303, FR-305, FR-306 | Assert golden counts and required semantic fields. |
| T-303 | FR-304 | Parameterized invalid-bundle rejection tests. |
| T-304 | FR-307 | CLI returns 0 for golden, non-zero for invalid fixture. |
