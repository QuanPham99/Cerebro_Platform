---
type: Entity
id: entity.card
title: Card
description: Business entity for card records.
status: stable
links:
- table.cards
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
  physical_mapping:
    table: table.cards
    key:
    - card_id
  grain:
    type: entity
    description: one row per issued bank card
    key:
    - card_id
  warnings: []
---

# Card

Card is represented by one `cards` record at its declared grain.
