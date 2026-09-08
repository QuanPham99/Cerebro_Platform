---
type: Metric
id: metric.late-payment-rate
title: Late Payment Rate
description: Percentage of loan-payment events flagged late.
status: stable
links:
- dimension.loan-type
- entity.loan-payment
- table.loan_payments
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
  entity: entity.loan-payment
  measure:
    kind: ratio
    numerator:
      kind: aggregate
      aggregation: count
      source: null
      predicates:
      - source:
          table: table.loan_payments
          column: late_payment_flag
        operator: eq
        value: 1
    denominator:
      kind: aggregate
      aggregation: count
      source: null
      predicates: []
    scale: 100.0
  dependencies:
  - table.loan_payments
  formula: 100 * SUM(CASE WHEN loan_payments.late_payment_flag = 1 THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*), 0)
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.loan-type
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Preserve payment-event denominator before joining loans.
---

# Late Payment Rate

Percentage of loan-payment events flagged late.

Formula: `100 * SUM(CASE WHEN loan_payments.late_payment_flag = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)`
