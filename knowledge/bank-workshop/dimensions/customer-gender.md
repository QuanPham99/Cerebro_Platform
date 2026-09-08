---
type: Dimension
id: dimension.customer-gender
title: Customer Gender
description: Governed customer gender dimension.
status: stable
links:
- entity.customer
- metric.customer-count
- metric.customer-loan-repayment-total
- metric.customer-net-cash-flow
- metric.supported-delinquency-population
- table.customers
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: dimension
  classification: restricted
  entity: entity.customer
  physical_mappings:
  - table: table.customers
    column: gender
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.customer-count
  - metric.customer-net-cash-flow
  - metric.customer-loan-repayment-total
  - metric.supported-delinquency-population
  warnings: []
---

# Customer Gender

Groups results by customer gender.
