---
type: metric
id: metric.late-payment-rate
name: Late payment rate
description: Percentage of loan payment events flagged late.
status: active
aliases:
- late payments by loan type
- payment delinquency rate
tags:
- metric
- banking
links: &id001
- table.loan_payments
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  classification: confidential
  dependencies: *id001
  formula: 100.0 * SUM(loan_payments.late_payment_flag) / NULLIF(COUNT(*), 0)
  metric_result_type: decimal
  filters: []
  grain: aggregate over loan payment events
  warnings:
  - Join loans only after preserving payment-event denominator.
---

# Late payment rate

Percentage of loan payment events flagged late.

Formula: `100.0 * SUM(loan_payments.late_payment_flag) / NULLIF(COUNT(*), 0)`
