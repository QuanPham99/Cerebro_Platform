---
type: Entity
id: entity.card-transaction
title: Card Transaction
description: Business entity for card transaction records.
status: stable
links:
- table.card_transactions
- domain.cards
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
  domain: domain.cards
  physical_mapping:
    table: table.card_transactions
    key:
    - card_txn_id
  grain:
    type: event
    description: one purchase or withdrawal event per card
    key:
    - card_txn_id
  warnings: []
---

# Card Transaction

Card Transaction is represented by one `card_transactions` record at its declared grain.
