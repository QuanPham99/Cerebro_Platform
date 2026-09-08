---
type: Table
id: table.employees
title: Employees
description: Employees assigned to branches, including Loan Officers.
status: stable
links:
- dataset.bank-workshop
- relationship.employee_branch
sources:
- id: duckdb-catalog
  resource: DuckDB information_schema
  title: DuckDB catalog metadata
provenance:
  origin: discovered
  source: DuckDB information_schema
cerebro:
  kind: physical_table
  classification: restricted
  physical:
    schema: main
    table: employees
  schema: main
  grain: one row per bank employee
  primary_key: employee_id
  columns:
  - name: employee_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: name
    data_type: VARCHAR
    nullable: true
    classification: restricted
    provenance: discovered
  - name: branch_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: role
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: hire_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: salary
    data_type: BIGINT
    nullable: true
    classification: confidential
    provenance: discovered
  warnings: []
resource: duckdb://bank/main/employees
---

# Employees

Employees assigned to branches, including Loan Officers.

Grain: **one row per bank employee**.
