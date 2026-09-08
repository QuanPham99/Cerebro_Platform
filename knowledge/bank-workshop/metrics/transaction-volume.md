---
type: metric
id: metric.transaction-volume
name: Transaction volume
description: Total positive account transaction amount for a defined period and scope.
status: active
aliases:
- transaction amount
- monthly volume
- total transactions
tags:
- metric
- banking
links: &id001
- table.transactions
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  classification: confidential
  dependencies: *id001
  formula: SUM(transactions.amount)
  metric_result_type: decimal
  filters: []
  grain: requested dimensions over account transaction events
  warnings:
  - Direction requires txn_type; amount itself is unsigned.
  - Use MAX(txn_date) as the relative-time anchor.
---

# Transaction volume

Total positive account transaction amount for a defined period and scope.

Formula: `SUM(transactions.amount)`
