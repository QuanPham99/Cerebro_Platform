---
type: Dimension
id: dimension.branch
title: Branch
description: Governed branch dimension.
status: stable
links:
- entity.branch
- metric.account-balance
- metric.branch-fraud-exposure
- metric.branch-toi-proxy
- metric.customer-loan-repayment-total
- metric.customer-net-cash-flow
- metric.non-performing-loan-rate
- metric.supported-delinquency-population
- metric.transaction-volume
- table.branches
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
  entity: entity.branch
  physical_mappings:
  - table: table.branches
    column: branch_name
  semantic_type: geographic
  derivation: null
  compatible_metrics:
  - metric.transaction-volume
  - metric.account-balance
  - metric.non-performing-loan-rate
  - metric.customer-net-cash-flow
  - metric.branch-fraud-exposure
  - metric.branch-toi-proxy
  - metric.customer-loan-repayment-total
  - metric.supported-delinquency-population
  warnings: []
---

# Branch

Groups results by branch.
