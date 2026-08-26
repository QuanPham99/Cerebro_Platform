# 007 — End-to-End Demo

## Problem

Independent components do not prove that a reviewer can move from a real schema to explainable semantic grounding in a short hackathon demo.

## Goal

Provide a reproducible, credential-free workflow that validates the golden bundle, serves retrieval/MCP, and opens a working graph explorer.

## Non-Goals

- Production deployment, authentication, HA, or operational SLAs.
- Benchmarking model quality at scale.

## Functional Requirements

- FR-601: Document installation, scan, generation/fallback, validation, test, build, and serve commands.
- FR-602: Provide a single development startup command for API, MCP, and UI.
- FR-603: Check in ten golden questions with required semantic objects/joins.
- FR-604: Run an automated evaluation and emit human-readable results.
- FR-605: Preserve a clean-source path: fresh checkout plus dependencies can start without provider credentials.
- FR-606: Produce a final compliance matrix for C-01 through C-12.

## Acceptance Criteria

- AC-601: A reviewer can complete the primary demo flow in under five minutes after dependencies are installed.
- AC-602: Tests, validation, evaluation, and production UI build pass.
- AC-603: The demo visibly connects a question to concepts, safe joins, grain, warnings, and provenance.
- AC-604: README clearly separates golden fallback from live AI generation.

## Edge Cases

- Missing source database, absent API key, occupied port, or frontend API unavailable.

## Interfaces / Contracts

Root task commands and `evaluation/golden-questions.yaml`; final report at `docs/implementation-compliance.md`.

## Constraints

Local Linux is the reference environment; all runtime outputs stay ignored.

## Assumptions

Python 3.11+, Node 20+, and npm are installed.

## Open Questions

Hosted demo packaging is deferred.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-601 | FR-601, FR-602 | Execute documented commands from the feature branch. |
| T-602 | FR-603, FR-604 | Evaluation passes all golden cases. |
| T-603 | FR-605 | Unset provider key and start/health-check the app. |
| T-604 | FR-606 | Report lists evidence and status for every compliance ID. |
