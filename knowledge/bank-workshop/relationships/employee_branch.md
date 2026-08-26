---
type: relationship
id: relationship.employee_branch
name: Employee Branch
description: Declared join from employees.branch_id to branches.branch_id.
status: active
tags:
- join
- physical-fk
links:
- table.employees
- table.branches
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.employees
  source_column: branch_id
  target_table: table.branches
  target_column: branch_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Employee Branch

Use an exact ID join with `many-to-one` cardinality.
