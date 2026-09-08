---
type: Business Rule
id: rule.late-loan-payment
title: Late Loan Payment
description: A loan payment is late when loan_payments.late_payment_flag equals 1.
status: stable
links:
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
  kind: business_rule
  classification: confidential
  entity: entity.loan-payment
  rule_kind: predicate
  output_type: boolean
  dependencies:
  - table.loan_payments
  logic: A loan payment is late when loan_payments.late_payment_flag equals 1.
  grain:
    type: entity
    description: One loan payment
  warnings: []
---

# Late Loan Payment

A loan payment is late when loan_payments.late_payment_flag equals 1.
