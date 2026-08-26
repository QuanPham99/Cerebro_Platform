# AI-rules.md

## Purpose

This repository uses **strict Spec-Driven Development (SDD)**.

For every non-trivial feature, bug fix, refactor, API/schema change, or behavior change, the AI MUST follow:

> **Discover → Specify → Design Tests → Implement → Verify → Report**

The specification is the source of truth. Tests are executable evidence that the implementation satisfies it.

---

## 1. Non-Negotiable Rules

The terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

The AI MUST:

1. Inspect relevant code, tests, contracts, schemas, and documentation before implementation.
2. Define or update the feature specification before changing production behavior.
3. Define explicit, testable acceptance criteria.
4. Design tests for every requirement before feature completion.
5. Trace implementation and tests back to requirement IDs.
6. Run applicable verification checks.
7. Report verification evidence, assumptions, skipped checks, and risks.

The AI MUST NOT:

- Start non-trivial implementation without a specification.
- Invent material product requirements.
- Weaken/delete tests merely to make code pass.
- Claim completion without required verification.
- Claim tests passed without actually running them.
- Silently introduce breaking API, schema, data, security, or compatibility changes.
- Mix unrelated refactoring into the requested feature.
- Backfill a specification after coding merely to justify the implementation.

---

## 2. Phase 0 — Discovery

Before modifying code, inspect enough of the repository to understand:

- relevant modules and entry points;
- architecture and boundaries;
- existing conventions;
- existing tests/specifications;
- APIs, schemas, and persistence models;
- dependencies affected by the change.

Reuse existing patterns unless the specification requires otherwise.

---

## 3. Phase 1 — Specification

For each non-trivial change, create or update:

```text
specs/<feature-name>.md
```

Required structure:

```markdown
# Feature: <name>

## Problem
...

## Goal
...

## Non-Goals
...

## Functional Requirements
- FR-1:
- FR-2:

## Acceptance Criteria
- AC-1:
- AC-2:

## Edge Cases
- EC-1:
- EC-2:

## Interfaces / Contracts
...

## Constraints
...

## Assumptions
...

## Open Questions
...
```

Requirements MUST be observable and testable.

Example:

```text
FR-1: The API MUST reject requests without customer_id.
AC-1: Missing customer_id returns HTTP 400 with error code CUSTOMER_ID_REQUIRED.
```

---

## 4. Specification Gate

The AI MUST NOT implement if:

1. critical behavior is ambiguous;
2. reasonable interpretations produce materially different behavior;
3. acceptance criteria cannot be determined;
4. a required external contract is unknown;
5. the request conflicts with an approved specification.

When blocked:

```text
SPEC BLOCKED
Reason:
Required decision:
Affected requirements:
```

Minor, reversible assumptions MAY be made, but MUST be recorded.

---

## 5. Phase 2 — Test Design

Every non-trivial feature MUST include a test design before implementation is considered complete.

Use either:

```text
specs/<feature-name>.tests.md
```

or a `## Test Design` section in the feature specification.

Minimum test matrix:

| Test ID | Requirement | Level | Scenario | Expected Result |
|---|---|---|---|---|
| T-01 | FR-1 / AC-1 | Unit | Missing customer_id | Validation error |
| T-02 | FR-2 / AC-2 | Integration | Valid request | Record persisted |
| T-03 | EC-1 | Unit | Duplicate request | Idempotent result |

Every acceptance criterion MUST map to at least one test or explicit verification method.

For every feature, evaluate:

- Happy path
- Validation
- Edge cases
- Error handling
- Regression
- Integration
- Contract/schema
- Security
- Performance

Mark non-applicable categories as `N/A`; do not silently omit them.

---

## 6. Test-First Rule

For behavior changes:

1. Write or identify the test representing the required behavior.
2. Confirm failure for the expected reason when practical.
3. Implement the smallest correct change.
4. Run the targeted test.
5. Run relevant regression tests.
6. Refactor only while tests remain green.

For bug fixes, add a regression test reproducing the bug whenever technically practical.

No regression test requires an explicit justification.

---

## 7. Phase 3 — Implementation

Implementation MUST:

- satisfy the written specification;
- remain within scope;
- follow repository conventions;
- minimize unnecessary changes;
- preserve backward compatibility unless explicitly waived;
- avoid speculative abstractions;
- avoid unrelated cleanup.

Maintain traceability where practical:

```text
FR-1 -> src/customer/service.py::create_customer
AC-1 -> tests/customer/test_validation.py::test_missing_customer_id
```

---

## 8. Change Control

If implementation reveals that the specification is wrong or incomplete:

1. STOP expanding implementation.
2. Update the specification.
3. Update acceptance criteria.
4. Update test design.
5. Resume only after artifacts are consistent.

The AI MUST NOT silently change intended behavior because implementation is difficult.

---

## 9. Phase 4 — Verification

After implementation, run all applicable repository checks:

- targeted tests;
- relevant integration/regression tests;
- lint/format checks;
- type checks;
- build;
- security/static analysis.

Never fabricate results.

If a check cannot be run, report:

```text
NOT VERIFIED LOCALLY
```

with the exact reason and remaining verification needed.

---

## 10. Definition of Done

A non-trivial feature is complete only when:

- [ ] Specification exists or was updated.
- [ ] Functional requirements are explicit.
- [ ] Acceptance criteria are explicit.
- [ ] Edge cases were evaluated.
- [ ] Test design maps tests to requirements.
- [ ] Required tests were implemented.
- [ ] New tests pass.
- [ ] Relevant existing tests pass.
- [ ] Applicable lint/type/build checks pass.
- [ ] Contracts/documentation were updated where required.
- [ ] No unexplained test failures remain.
- [ ] Implementation matches the specification.
- [ ] Assumptions, skipped checks, and risks are reported.

If a required item is incomplete, the AI MUST NOT claim the feature is complete.

---

## 11. Required Pre-Implementation Output

Before coding a non-trivial feature, produce:

```markdown
## Feature Contract

### Goal
...

### Requirements
- FR-1 ...
- FR-2 ...

### Acceptance Criteria
- AC-1 ...
- AC-2 ...

### Test Plan
- T-01 ...
- T-02 ...

### Expected Files to Change
- ...

### Assumptions / Risks
- ...
```

If the requirements are clear, proceed without repeatedly asking for confirmation.

---

## 12. Required Completion Report

Use:

```markdown
## Implementation Summary
- ...

## Requirement Traceability
- FR-1 -> ...
- FR-2 -> ...

## Verification
- T-01: PASS
- T-02: PASS

## Commands Run
- `<command>`

## Not Run
- `<check>` — `<reason>`

## Known Risks
- None identified.
```

`PASS` may only be reported when supported by actual execution or valid direct inspection.

---

## 13. Small-Change Exception

A standalone specification file is optional only for clearly trivial changes such as:

- typo corrections;
- formatting;
- comment-only changes;
- documentation-only changes;
- mechanical renaming with no behavior change.

When uncertain, treat the change as non-trivial.

---

## 14. Forbidden Anti-Patterns

The AI MUST NOT:

- code first and write the spec afterward;
- use vague criteria such as "works correctly";
- test only implementation internals;
- over-mock integration boundaries;
- delete failing assertions to obtain green tests;
- change expected outputs solely to match incorrect behavior;
- omit known edge cases;
- hide breaking changes;
- add unnecessary dependencies;
- leave acceptance-critical TODOs while claiming completion;
- report `"all tests pass"` without executing them.

---

## 15. Instruction Priority

When instructions conflict:

1. Explicit user requirement for the current task.
2. Approved feature specification.
3. Repository architecture and public contracts.
4. This `AI-rules.md`.
5. Existing implementation details.

Security, data-loss, incompatible-contract, and impossible-acceptance-criteria conflicts MUST be surfaced before proceeding.

---

## 16. Final Self-Check

Before claiming completion, verify:

1. What requirement does each material code change satisfy?
2. Does every acceptance criterion have implementation coverage?
3. Does every acceptance criterion have verification coverage?
4. Did changed behavior receive regression coverage?
5. Were relevant tests actually run?
6. Were existing contracts preserved unless explicitly changed?
7. Were assumptions, skipped checks, and risks reported?
8. Am I claiming anything I did not verify?

If any answer is unsatisfactory, continue working or report the incomplete item.

---

# 17. Compliance & Enforcement

Compliance with this file is mandatory for all AI-generated or AI-modified code in this repository.

The AI MUST treat SDD compliance as a release gate, not as optional guidance.

## 17.1 Compliance Status

Every non-trivial task MUST end with exactly one of these statuses:

```text
COMPLIANT
PARTIALLY COMPLIANT
NON-COMPLIANT
BLOCKED
```

Definitions:

- `COMPLIANT` — all applicable SDD, test, verification, and traceability requirements are satisfied.
- `PARTIALLY COMPLIANT` — implementation is usable, but one or more non-critical checks could not be completed.
- `NON-COMPLIANT` — required SDD controls were skipped or violated.
- `BLOCKED` — implementation cannot safely proceed because required information, dependencies, access, or decisions are missing.

The AI MUST NOT report `COMPLIANT` unless all mandatory gates below pass.

---

## 17.2 Mandatory Compliance Gates

For every non-trivial change, evaluate these gates:

| Gate | Requirement | Pass Condition |
|---|---|---|
| C-01 | Specification | Feature specification exists and reflects intended behavior |
| C-02 | Acceptance Criteria | Every functional requirement has explicit acceptance criteria |
| C-03 | Test Design | Test design exists and maps to requirements |
| C-04 | Implementation Traceability | Material code changes map to requirement IDs |
| C-05 | Test Coverage | Applicable requirements and edge cases have tests or explicit verification |
| C-06 | Regression Safety | Relevant existing behavior is covered and regression tests pass |
| C-07 | Static Quality | Applicable lint, type, format, build, and static checks pass |
| C-08 | Contract Safety | API/schema/event/public contract changes are explicitly documented and verified |
| C-09 | Security Review | Security-sensitive changes receive appropriate tests/review |
| C-10 | Verification Evidence | Commands and results are reported truthfully |
| C-11 | Scope Control | No unrelated material changes are included |
| C-12 | Documentation | User-facing or technical documentation is updated where required |

A failed mandatory gate prevents `COMPLIANT` status.

---

## 17.3 Requirement-to-Test Traceability

Every acceptance criterion MUST have at least one linked verification method.

Preferred format:

```text
FR-1
  └── AC-1
      ├── T-01 unit
      └── T-02 integration

FR-2
  └── AC-2
      └── T-03 contract
```

A requirement without verification coverage is considered a compliance failure unless explicitly marked as non-testable with justification.

---

## 17.4 Compliance Matrix

Before completion, produce a compliance matrix for non-trivial work:

| ID | Control | Status | Evidence |
|---|---|---|---|
| C-01 | Specification | PASS / FAIL / N/A | `specs/<feature>.md` |
| C-02 | Acceptance Criteria | PASS / FAIL / N/A | AC identifiers |
| C-03 | Test Design | PASS / FAIL / N/A | test matrix |
| C-04 | Traceability | PASS / FAIL / N/A | requirement → code mapping |
| C-05 | Test Coverage | PASS / FAIL / N/A | test names |
| C-06 | Regression Safety | PASS / FAIL / N/A | regression command |
| C-07 | Static Quality | PASS / FAIL / N/A | lint/type/build commands |
| C-08 | Contract Safety | PASS / FAIL / N/A | contract diff or tests |
| C-09 | Security Review | PASS / FAIL / N/A | security tests/review |
| C-10 | Verification Evidence | PASS / FAIL / N/A | commands executed |
| C-11 | Scope Control | PASS / FAIL / N/A | changed-files review |
| C-12 | Documentation | PASS / FAIL / N/A | documentation references |

`N/A` MUST include a brief justification.

---

## 17.5 Compliance Failure Behavior

If any mandatory compliance gate fails, the AI MUST:

1. Identify the failed control ID.
2. Explain why it failed.
3. State whether the failure blocks release or only reduces confidence.
4. Fix the failure if it is within scope and technically possible.
5. Re-run affected verification.
6. Never hide the failure behind a generic success summary.

Example:

```text
COMPLIANCE FAILURE: C-06

Reason:
Relevant regression suite could not run because PostgreSQL is unavailable.

Impact:
Feature behavior is locally validated, but regression safety is unverified.

Status:
PARTIALLY COMPLIANT
```

---

## 17.6 Compliance Exceptions

Exceptions are allowed only when technically necessary.

Every exception MUST include:

```text
Exception ID:
Affected Control:
Reason:
Risk:
Compensating Control:
Owner / Decision Source:
Expiry or Follow-up:
```

The AI MUST NOT invent approval for an exception.

If explicit approval does not exist, label it:

```text
UNAPPROVED EXCEPTION
```

An unapproved exception prevents `COMPLIANT` status.

---

## 17.7 Machine-Enforceable Repository Controls

Where repository tooling permits, the AI SHOULD create or preserve automated compliance checks.

Recommended controls:

### Pull Request / CI Gates

CI SHOULD fail when:

- required specification files are missing for feature changes;
- required tests are absent;
- tests fail;
- lint/type/build checks fail;
- generated schemas/contracts are inconsistent;
- coverage falls below repository thresholds;
- required documentation validation fails.

Example conceptual pipeline:

```text
spec-check
    ↓
test-design-check
    ↓
unit-tests
    ↓
integration-tests
    ↓
lint
    ↓
type-check
    ↓
build
    ↓
contract-check
    ↓
compliance-check
```

The AI MUST NOT bypass failing CI checks by disabling them unless the user explicitly requests a justified repository-level policy change.

---

## 17.8 Suggested Repository Compliance Structure

Recommended structure:

```text
.
├── AI-rules.md
├── specs/
│   ├── feature-a.md
│   └── feature-a.tests.md
├── src/
├── tests/
├── scripts/
│   └── check_sdd_compliance.*
└── .github/
    └── workflows/
        └── sdd-compliance.yml
```

Equivalent paths MAY be used for GitLab CI, Bitbucket Pipelines, Jenkins, or another CI platform.

---

## 17.9 AI Self-Enforcement

Before editing production code, the AI MUST internally check:

```text
SPEC EXISTS?
    NO  → CREATE/UPDATE SPEC FIRST
    YES ↓

ACCEPTANCE CRITERIA TESTABLE?
    NO  → BLOCK OR RESOLVE
    YES ↓

TEST DESIGN EXISTS?
    NO  → CREATE TEST DESIGN FIRST
    YES ↓

IMPLEMENT
    ↓

VERIFY
    ↓

COMPLIANCE MATRIX
    ↓

DECLARE STATUS
```

The AI MUST NOT skip directly from request interpretation to implementation for non-trivial work.

---

## 17.10 Final Compliance Declaration

Every non-trivial completion report MUST end with:

```markdown
## Compliance Status

**Status:** COMPLIANT | PARTIALLY COMPLIANT | NON-COMPLIANT | BLOCKED

### Failed / Skipped Controls
- None

### Exceptions
- None

### Residual Risks
- None identified
```

If the status is anything other than `COMPLIANT`, the AI MUST clearly state what is missing before the work can be considered fully complete.

---

# 18. Strengthened Core Enforcement Rule

> **No specification → no implementation.**  
> **No acceptance criteria → no implementation.**  
> **No test design → no feature completion.**  
> **No verification evidence → no success claim.**  
> **Failed mandatory compliance gate → no COMPLIANT status.**

The AI MUST optimize for **verified requirement satisfaction and compliance**, not for code-generation speed.
