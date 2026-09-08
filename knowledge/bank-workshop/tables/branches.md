---
type: Table
id: table.branches
title: Branches
description: Physical bank branches and routing identifiers.
status: stable
links:
- dataset.bank-workshop
- relationship.account_branch
- relationship.employee_branch
- relationship.loan_branch
sources:
- id: duckdb-catalog
  resource: DuckDB information_schema
  title: DuckDB catalog metadata
provenance:
  origin: discovered
  source: DuckDB information_schema
cerebro:
  kind: physical_table
  classification: internal
  physical:
    schema: main
    table: branches
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
resource: duckdb://bank/main/branches
---

# Branches

Physical bank branches and routing identifiers.

Grain: **one row per bank branch**.
