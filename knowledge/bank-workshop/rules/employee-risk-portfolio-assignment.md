---
type: Business Rule
id: rule.employee-risk-portfolio-assignment
title: Employee Risk Portfolio Assignment
description: Flag an employee assignment for review when the employee's branch owns
  at least three defaulted or written-off loans held by customers with credit scores
  below 600; join employees to branches, branches to loans, and loans to customers.
status: stable
links:
- entity.employee
- table.branches
- table.customers
- table.employees
- table.loans
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: business_rule
  classification: confidential
  entity: entity.employee
  rule_kind: classification
  output_type: boolean
  dependencies:
  - table.employees
  - table.branches
  - table.loans
  - table.customers
  logic: Flag an employee assignment for review when the employee's branch owns at
    least three defaulted or written-off loans held by customers with credit scores
    below 600; join employees to branches, branches to loans, and loans to customers.
  grain:
    type: entity
    description: One employee
  warnings: []
---

# Employee Risk Portfolio Assignment

Flag an employee assignment for review when the employee's branch owns at least three defaulted or written-off loans held by customers with credit scores below 600; join employees to branches, branches to loans, and loans to customers.
