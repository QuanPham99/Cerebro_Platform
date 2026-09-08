---
type: Business Rule
id: rule.relative-time-anchor
title: Relative Time Anchor
description: Interpret relative account-transaction periods from MAX(transactions.txn_date),
  not wall-clock time.
status: stable
links:
- dimension.transaction-date
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
  rule_kind: time_anchor
  output_type: date
  dependencies:
  - dimension.transaction-date
  logic: Interpret relative account-transaction periods from MAX(transactions.txn_date),
    not wall-clock time.
  grain:
    type: entity
    description: One transaction
  warnings: []
---

# Relative Time Anchor

Interpret relative account-transaction periods from MAX(transactions.txn_date), not wall-clock time.
