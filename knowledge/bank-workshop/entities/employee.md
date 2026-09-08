---
type: Entity
id: entity.employee
title: Employee
description: Business entity for employee records.
status: stable
links:
- table.employees
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: entity
  classification: internal
  physical_mapping:
    table: table.employees
    key:
    - employee_id
  grain:
    type: entity
    description: one row per bank employee
    key:
    - employee_id
  warnings: []
---

# Employee

Employee is represented by one `employees` record at its declared grain.
