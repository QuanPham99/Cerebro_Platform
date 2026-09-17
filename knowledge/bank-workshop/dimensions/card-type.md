---
type: Dimension
id: dimension.card-type
title: Card Type
description: Governed card type dimension.
aliases:
- "loại thẻ"
status: stable
links:
- entity.card
- metric.branch-fraud-exposure
- metric.card-fraud-rate
- table.cards
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: dimension
  classification: internal
  entity: entity.card
  physical_mappings:
  - table: table.cards
    column: card_type
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.card-fraud-rate
  - metric.branch-fraud-exposure
  warnings: []
---

# Card Type

Groups results by card type.
