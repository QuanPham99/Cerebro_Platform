# 001 — Google OKF Baseline

## Problem

Cerebro needs an auditable relationship to Google's OKF implementation without allowing upstream code changes to become hidden product behavior.

## Goal

Vendor the exact upstream repository as an unmodified Git subtree and reuse its document/source contracts.

## Non-Goals

- Tracking upstream `main` automatically.
- Modifying Google ADK, BigQuery, or viewer code.
- Requiring Google Cloud at runtime.

## Functional Requirements

- FR-001: Place the complete upstream tree at `vendor/open-knowledge-format`.
- FR-002: Record repository URL and commit SHA in project metadata.
- FR-003: Cerebro adapters may import upstream contracts but live outside the subtree.
- FR-004: Upstream-dependent tests must be runnable without Google credentials.

## Acceptance Criteria

- AC-001: The subtree commit resolves to `ad30107c31c06aec8a7d5636e0d1058118604e6f`.
- AC-002: `git diff` reports no Cerebro-authored changes inside the subtree after import.
- AC-003: A smoke test parses and validates a minimal OKF document using upstream code.

## Edge Cases

- Upstream optional dependencies are unavailable.
- The upstream package is not installed editable.

## Interfaces / Contracts

`reference_agent.bundle.OKFDocument`, `reference_agent.sources.base.Source`, and `ConceptRef` are the reused public contracts.

## Constraints

Python 3.11+; subtree remains unmodified.

## Assumptions

The pinned upstream license permits vendoring.

## Open Questions

None for the prototype.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-001 | FR-001, FR-002 | Inspect vendor metadata and Git subtree commit. |
| T-002 | FR-003 | Assert adapter subclasses upstream `Source`. |
| T-003 | FR-004 | Parse/validate minimal document in an offline test. |
