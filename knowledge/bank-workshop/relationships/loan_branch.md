---
type: Relationship
id: relationship.loan_branch
title: Loan Branch
description: Declared join from loans.branch_id to branches.branch_id.
status: stable
links:
- entity.branch
- entity.loan
- table.branches
- table.loans
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: relationship
  classification: internal
  edge_type: physical_fk
  semantic:
    from: entity.loan
    to: entity.branch
  physical:
    source:
      table: table.loans
      column: branch_id
    target:
      table: table.branches
      column: branch_id
  source_table: table.loans
  source_column: branch_id
  target_table: table.branches
  target_column: branch_id
  cardinality: many-to-one
  join_type:
    default: left
  validation:
    target_unique: not_checked
    source_fk_coverage: not_checked
    fanout: not_checked
  warnings:
  - Declared relationship; source-row profiling is disabled.
---

# Loan Branch

Use the declared `many-to-one` join.
