---
type: Dimension
id: dimension.account-type
title: Account Type
description: Governed account type dimension.
status: stable
links:
- entity.account
- metric.account-balance
- table.accounts
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
  entity: entity.account
  physical_mappings:
  - table: table.accounts
    column: account_type
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.account-balance
  warnings: []
---

# Account Type

Groups results by account type.
