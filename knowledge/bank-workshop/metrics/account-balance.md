---
type: Metric
id: metric.account-balance
title: Account Balance
description: Average current account balance within the requested dimensional scope.
aliases:
- "số dư"
- "số dư tài khoản"
status: stable
links:
- dimension.account-type
- dimension.branch
- entity.account
- table.accounts
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
  entity: entity.account
  measure:
    kind: aggregate
    aggregation: avg
    source:
      table: table.accounts
      column: balance
    predicates: []
  dependencies:
  - table.accounts
  formula: AVG(accounts.balance)
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.account-type
  - dimension.branch
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Balance is a current snapshot, not a transaction flow.
---

# Account Balance

Average current account balance within the requested dimensional scope.

Formula: `AVG(accounts.balance)`
