---
type: Relationship
id: relationship.card_customer
title: Card Customer
description: Declared join from cards.customer_id to customers.customer_id.
status: stable
links:
- entity.card
- entity.customer
- table.cards
- table.customers
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: relationship
  classification: internal
  edge_type: physical_fk
  semantic:
    from: entity.card
    to: entity.customer
  physical:
    source:
      table: table.cards
      column: customer_id
    target:
      table: table.customers
      column: customer_id
  source_table: table.cards
  source_column: customer_id
  target_table: table.customers
  target_column: customer_id
  cardinality: many-to-one
  join_type:
    default: left
  validation:
    target_unique: not_checked
    source_fk_coverage: not_checked
    fanout: not_checked
  warnings:
  - Declared relationship; source-row profiling is disabled.
---

# Card Customer

Use the declared `many-to-one` join.
