---
type: Dimension
id: dimension.transaction-type
title: Transaction Type
description: Governed transaction type dimension.
status: stable
links:
- entity.transaction
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
  warnings: []
---

# Transaction Type

Groups results by transaction type.
