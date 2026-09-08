---
type: Dimension
id: dimension.merchant-category
title: Merchant Category
description: Governed merchant category dimension.
status: stable
links:
- entity.card-transaction
- metric.branch-fraud-exposure
- metric.card-fraud-rate
- table.card_transactions
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
  entity: entity.card-transaction
  physical_mappings:
  - table: table.card_transactions
    column: merchant_category
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.card-fraud-rate
  - metric.branch-fraud-exposure
  warnings: []
---

# Merchant Category

Groups results by merchant category.
