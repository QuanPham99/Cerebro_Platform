---
type: Dimension
id: dimension.loan-type
title: Loan Type
description: Governed loan type dimension.
status: stable
links:
- entity.loan
- metric.customer-loan-repayment-total
- metric.late-payment-rate
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
    column: loan_type
  semantic_type: categorical
  derivation: null
  compatible_metrics:
  - metric.late-payment-rate
  - metric.non-performing-loan-rate
  - metric.customer-loan-repayment-total
  - metric.supported-delinquency-population
  warnings: []
---

# Loan Type

Groups results by loan type.
