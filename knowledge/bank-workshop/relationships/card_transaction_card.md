---
type: Relationship
id: relationship.card_transaction_card
title: Card Transaction Card
description: Declared join from card_transactions.card_id to cards.card_id.
status: stable
links:
- entity.card
- entity.card-transaction
- table.card_transactions
- table.cards
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
    from: entity.card-transaction
    to: entity.card
  physical:
    source:
      table: table.card_transactions
      column: card_id
    target:
      table: table.cards
      column: card_id
  source_table: table.card_transactions
  source_column: card_id
  target_table: table.cards
  target_column: card_id
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

# Card Transaction Card

Use the declared `many-to-one` join.
