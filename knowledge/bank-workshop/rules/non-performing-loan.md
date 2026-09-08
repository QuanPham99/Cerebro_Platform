---
type: Business Rule
id: rule.non-performing-loan
title: Non-performing Loan
description: A loan is non-performing when loans.status is Defaulted or Written Off.
status: stable
links:
- entity.loan
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
  entity: entity.loan
  rule_kind: predicate
  output_type: boolean
  dependencies:
  - table.loans
  logic: A loan is non-performing when loans.status is Defaulted or Written Off.
  grain:
    type: entity
    description: One loan
  warnings: []
---

# Non-performing Loan

A loan is non-performing when loans.status is Defaulted or Written Off.
