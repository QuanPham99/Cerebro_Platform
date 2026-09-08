---
type: Dimension
id: dimension.loan-status
title: Loan Status
description: Governed loan status dimension.
status: stable
links:
- entity.loan
- metric.customer-loan-repayment-total
- metric.non-performing-loan-rate
- metric.supported-delinquency-population
- table.loans
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
  entity: entity.loan
  physical_mappings:
  - table: table.loans
    column: status
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.non-performing-loan-rate
  - metric.customer-loan-repayment-total
  - metric.supported-delinquency-population
  warnings: []
---

# Loan Status

Groups results by loan status.
