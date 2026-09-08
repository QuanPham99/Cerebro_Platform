---
type: Dimension
id: dimension.customer-age
title: Customer Age
description: Governed customer age dimension.
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
    column: date_of_birth
  semantic_type: derived
  derivation: Completed years from date_of_birth at the relevant maximum available
    date.
  compatible_metrics:
  - metric.customer-count
  - metric.customer-net-cash-flow
  - metric.customer-loan-repayment-total
  - metric.supported-delinquency-population
  warnings: []
---

# Customer Age

Completed years from date_of_birth at the relevant maximum available date.
