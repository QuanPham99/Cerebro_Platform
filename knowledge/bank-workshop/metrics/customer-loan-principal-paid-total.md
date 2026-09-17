---
type: Metric
id: metric.customer-loan-principal-paid-total
title: Customer Loan Principal Paid Total
description: Total loan principal repaid by customer, loan attributes, and originating
  branch.
aliases:
- "số tiền gốc đã trả"
- "gốc đã thanh toán"
status: stable
links:
- dimension.branch
- dimension.customer-age
- dimension.customer-gender
- dimension.loan-status
- dimension.loan-type
- entity.customer
- table.branches
- table.customers
- table.loan_payments
- table.loans
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
    aggregation: sum
    source:
      table: table.loan_payments
      column: principal_component
    predicates: []
  dependencies:
  - table.customers
  - table.loans
  - table.loan_payments
  - table.branches
  formula: SUM(loan_payments.principal_component) FROM customers JOIN loans ON customers.customer_id
    = loans.customer_id JOIN loan_payments ON loans.loan_id = loan_payments.loan_id
    JOIN branches ON loans.branch_id = branches.branch_id
  metric_result_type: decimal
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
  - Aggregate from loan-payment grain; customer and branch are many-to-one lookup
    joins. Remaining/outstanding loan balance is loans.loan_amount minus this metric,
    computed per loan.
---

# Customer Loan Principal Paid Total

Total loan principal repaid by customer, loan attributes, and originating branch.

Formula: `SUM(loan_payments.principal_component) FROM customers JOIN loans ON customers.customer_id = loans.customer_id JOIN loan_payments ON loans.loan_id = loan_payments.loan_id JOIN branches ON loans.branch_id = branches.branch_id`

A loan's remaining/outstanding balance is `loans.loan_amount` minus this metric's value for
that loan.
