---
type: Metric
id: metric.card-fraud-rate
title: Card Fraud Rate
description: Percentage of card transactions flagged as fraud.
status: stable
links:
- dimension.card-type
- dimension.merchant-category
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
  kind: metric
  classification: confidential
  entity: entity.card-transaction
  measure:
    kind: ratio
    numerator:
      kind: aggregate
      aggregation: count
      source: null
      predicates:
      - source:
          table: table.card_transactions
          column: is_fraud
        operator: eq
        value: 1
    denominator:
      kind: aggregate
      aggregation: count
      source: null
      predicates: []
    scale: 100.0
  dependencies:
  - table.card_transactions
  formula: 100 * SUM(CASE WHEN card_transactions.is_fraud = 1 THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0)
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.merchant-category
  - dimension.card-type
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Use card-transaction grain.
---

# Card Fraud Rate

Percentage of card transactions flagged as fraud.

Formula: `100 * SUM(CASE WHEN card_transactions.is_fraud = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)`
