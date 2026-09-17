---
type: Dimension
id: dimension.transaction-type
title: Transaction Type
description: Governed transaction type dimension.
aliases:
- "loại giao dịch"
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
    column: txn_type
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.transaction-volume
  - metric.customer-net-cash-flow
  - metric.branch-toi-proxy
  warnings: []
---

# Transaction Type

Groups results by transaction type.
