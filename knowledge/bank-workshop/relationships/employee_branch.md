---
type: Relationship
id: relationship.employee_branch
title: Employee Branch
description: Declared join from employees.branch_id to branches.branch_id.
status: stable
links:
- entity.branch
- entity.employee
- table.branches
- table.employees
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
    from: entity.employee
    to: entity.branch
  physical:
    source:
      table: table.employees
      column: branch_id
    target:
      table: table.branches
      column: branch_id
  source_table: table.employees
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

# Employee Branch

Use the declared `many-to-one` join.
