---
type: relationship
id: relationship.loan_branch
name: Loan Branch
description: Declared join from loans.branch_id to branches.branch_id.
status: active
tags:
- join
- physical-fk
links:
- table.loans
- table.branches
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.loans
  source_column: branch_id
  target_table: table.branches
  target_column: branch_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Loan Branch

Use an exact ID join with `many-to-one` cardinality.
