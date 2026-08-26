---
type: table
id: table.branches
name: Branches
description: Physical bank branches and routing identifiers.
status: active
aliases:
- bank locations
- branch network
tags:
- banking
- branches
links:
- dataset.bank-workshop
- relationship.account_branch
- relationship.loan_branch
- relationship.employee_branch
- concept.branch-performance
- concept.bad-debt
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: internal
  schema: main
  grain: one row per bank branch
  primary_key: branch_id
  columns:
  - name: branch_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: branch_name
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: city
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: state
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: opened_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: ifsc_code
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
---

# Branches

Physical bank branches and routing identifiers.

Grain: **one row per bank branch**.
