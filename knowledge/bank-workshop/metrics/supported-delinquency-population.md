---
type: Metric
id: metric.supported-delinquency-population
title: Supported Delinquency Population
description: Distinct customers who have both a support ticket and at least one late
  loan payment.
status: stable
links:
- dimension.branch
- dimension.customer-age
- dimension.customer-gender
- dimension.loan-status
- dimension.loan-type
- entity.customer
- table.customers
- table.loan_payments
- table.loans
- table.support_tickets
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: metric
  classification: restricted
  entity: entity.customer
  measure:
    kind: aggregate
    aggregation: count_distinct
    source:
      table: table.customers
      column: customer_id
    predicates: []
  dependencies:
  - table.customers
  - table.support_tickets
  - table.loans
  - table.loan_payments
  formula: COUNT(DISTINCT customers.customer_id) FROM customers JOIN support_tickets
    ON customers.customer_id = support_tickets.customer_id JOIN loans ON customers.customer_id
    = loans.customer_id JOIN loan_payments ON loans.loan_id = loan_payments.loan_id
    WHERE loan_payments.late_payment_flag = 1
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.customer-gender
  - dimension.customer-age
  - dimension.branch
  - dimension.loan-type
  - dimension.loan-status
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Count distinct customers after joining two one-to-many paths to prevent ticket-payment
    fanout.
---

# Supported Delinquency Population

Distinct customers who have both a support ticket and at least one late loan payment.

Formula: `COUNT(DISTINCT customers.customer_id) FROM customers JOIN support_tickets ON customers.customer_id = support_tickets.customer_id JOIN loans ON customers.customer_id = loans.customer_id JOIN loan_payments ON loans.loan_id = loan_payments.loan_id WHERE loan_payments.late_payment_flag = 1`
