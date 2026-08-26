---
type: relationship
id: relationship.card_transaction_card
name: Card Transaction Card
description: Declared join from card_transactions.card_id to cards.card_id.
status: active
tags:
- join
- physical-fk
links:
- table.card_transactions
- table.cards
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.card_transactions
  source_column: card_id
  target_table: table.cards
  target_column: card_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Card Transaction Card

Use an exact ID join with `many-to-one` cardinality.
