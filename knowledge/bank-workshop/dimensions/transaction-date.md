---
type: Dimension
id: dimension.transaction-date
title: Transaction Date
description: Governed transaction date dimension.
aliases:
- "ngày giao dịch"
status: stable
links:
- entity.transaction
- metric.branch-toi-proxy
- metric.customer-net-cash-flow
- metric.transaction-volume
- table.transactions
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
  entity: entity.transaction
  physical_mappings:
  - table: table.transactions
    column: txn_date
  semantic_type: temporal
  derivation: null
  compatible_metrics:
  - metric.transaction-volume
  - metric.customer-net-cash-flow
  - metric.branch-toi-proxy
  warnings: []
---

# Transaction Date

Groups results by transaction date.
