---
type: Business Rule
id: rule.transaction-direction
title: Transaction Direction
description: Deposit, Transfer In, and Interest Credit are inflows; Withdrawal, Transfer
  Out, and Fee Debit are outflows.
status: stable
links:
- dimension.transaction-type
- entity.transaction
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
  entity: entity.transaction
  rule_kind: classification
  output_type: direction
  dependencies:
  - dimension.transaction-type
  logic: Deposit, Transfer In, and Interest Credit are inflows; Withdrawal, Transfer
    Out, and Fee Debit are outflows.
  grain:
    type: entity
    description: One transaction
  warnings: []
---

# Transaction Direction

Deposit, Transfer In, and Interest Credit are inflows; Withdrawal, Transfer Out, and Fee Debit are outflows.
