---
type: table
id: table.employees
name: Employees
description: Employees assigned to branches, including Loan Officers.
status: active
aliases:
- staff
- loan officers
- branch employees
tags:
- banking
- employees
links:
- dataset.bank-workshop
- relationship.employee_branch
- concept.branch-performance
- concept.bad-debt
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: restricted
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
---

# Employees

Employees assigned to branches, including Loan Officers.

Grain: **one row per bank employee**.
