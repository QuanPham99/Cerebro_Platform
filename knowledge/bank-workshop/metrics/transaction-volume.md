---
type: Metric
id: metric.transaction-volume
title: Transaction Volume
description: Total positive account transaction amount for a defined period and scope.
aliases:
- "tổng số tiền giao dịch"
- "khối lượng giao dịch"
- "tổng chi tiêu"
status: stable
links:
- dimension.branch
- dimension.transaction-channel
- dimension.transaction-date
- dimension.transaction-type
- entity.transaction
- table.transactions
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
  entity: entity.transaction
  measure:
    kind: aggregate
    aggregation: sum
    source:
      table: table.transactions
      column: amount
    predicates: []
  dependencies:
  - table.transactions
  formula: SUM(transactions.amount)
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.transaction-type
  - dimension.transaction-channel
  - dimension.transaction-date
  - dimension.branch
  time_dimension: dimension.transaction-date
  relative_time_anchor: max_available_date
  warnings:
  - Direction requires transaction type; amount itself is unsigned.
---

# Transaction Volume

Total positive account transaction amount for a defined period and scope.

Formula: `SUM(transactions.amount)`
