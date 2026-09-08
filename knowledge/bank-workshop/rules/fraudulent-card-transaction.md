---
type: Business Rule
id: rule.fraudulent-card-transaction
title: Fraudulent Card Transaction
description: A card transaction is fraudulent when card_transactions.is_fraud equals
  1.
status: stable
links:
- entity.card-transaction
- table.card_transactions
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
  entity: entity.card-transaction
  rule_kind: predicate
  output_type: boolean
  dependencies:
  - table.card_transactions
  logic: A card transaction is fraudulent when card_transactions.is_fraud equals 1.
  grain:
    type: entity
    description: One card transaction
  warnings: []
---

# Fraudulent Card Transaction

A card transaction is fraudulent when card_transactions.is_fraud equals 1.
