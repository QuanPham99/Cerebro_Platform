---
type: Metric
id: metric.customer-net-cash-flow
title: Customer Net Cash Flow
description: Net signed account-transaction amount by customer and servicing branch.
status: stable
links:
- dimension.branch
- dimension.customer-age
- dimension.customer-gender
- dimension.transaction-channel
- dimension.transaction-date
- dimension.transaction-type
- entity.customer
- table.accounts
- table.branches
- table.customers
- table.transactions
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
      table: table.transactions
      column: amount
    predicates: []
  dependencies:
  - table.customers
  - table.accounts
  - table.transactions
  - table.branches
  formula: SUM(CASE WHEN transactions.txn_type IN ('Deposit', 'Transfer In', 'Interest
    Credit') THEN transactions.amount ELSE -transactions.amount END) FROM customers
    JOIN accounts ON customers.customer_id = accounts.customer_id JOIN transactions
    ON accounts.account_id = transactions.account_id JOIN branches ON accounts.branch_id
    = branches.branch_id
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.customer-gender
  - dimension.customer-age
  - dimension.branch
  - dimension.transaction-type
  - dimension.transaction-channel
  - dimension.transaction-date
  time_dimension: dimension.transaction-date
  relative_time_anchor: max_available_date
  warnings:
  - Join customers to accounts before transactions; the branch join is many-to-one
    and does not change transaction grain.
---

# Customer Net Cash Flow

Net signed account-transaction amount by customer and servicing branch.

Formula: `SUM(CASE WHEN transactions.txn_type IN ('Deposit', 'Transfer In', 'Interest Credit') THEN transactions.amount ELSE -transactions.amount END) FROM customers JOIN accounts ON customers.customer_id = accounts.customer_id JOIN transactions ON accounts.account_id = transactions.account_id JOIN branches ON accounts.branch_id = branches.branch_id`
