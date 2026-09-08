---
type: Business Rule
id: rule.high-value-multichannel-customer
title: High-value Multichannel Customer
description: A customer qualifies when their accounts have at least one active card
  and at least 100000 in signed transaction activity across two or more channels during
  the latest 90-day period; join customers to accounts, then aggregate the transaction
  and card branches independently at customer grain.
status: stable
links:
- entity.customer
- table.accounts
- table.cards
- table.customers
- table.transactions
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: business_rule
  classification: restricted
  entity: entity.customer
  rule_kind: classification
  output_type: boolean
  dependencies:
  - table.customers
  - table.accounts
  - table.transactions
  - table.cards
  logic: A customer qualifies when their accounts have at least one active card and
    at least 100000 in signed transaction activity across two or more channels during
    the latest 90-day period; join customers to accounts, then aggregate the transaction
    and card branches independently at customer grain.
  grain:
    type: entity
    description: One customer
  warnings: []
---

# High-value Multichannel Customer

A customer qualifies when their accounts have at least one active card and at least 100000 in signed transaction activity across two or more channels during the latest 90-day period; join customers to accounts, then aggregate the transaction and card branches independently at customer grain.
