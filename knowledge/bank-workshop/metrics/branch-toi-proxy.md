---
type: Metric
id: metric.branch-toi-proxy
title: Branch TOI Proxy
description: Partial branch operating-income proxy calculated as fee debits less interest
  credits; it is not audited Total Operating Income.
status: stable
links:
- dimension.branch
- dimension.transaction-date
- dimension.transaction-type
- entity.branch
- table.accounts
- table.branches
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
  classification: confidential
  entity: entity.branch
  measure:
    kind: aggregate
    aggregation: sum
    source:
      table: table.transactions
      column: amount
    predicates: []
  dependencies:
  - table.branches
  - table.accounts
  - table.transactions
  formula: SUM(CASE WHEN transactions.txn_type = 'Fee Debit' THEN transactions.amount
    WHEN transactions.txn_type = 'Interest Credit' THEN -transactions.amount ELSE
    0 END) FROM branches JOIN accounts ON branches.branch_id = accounts.branch_id
    JOIN transactions ON accounts.account_id = transactions.account_id
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.branch
  - dimension.transaction-type
  - dimension.transaction-date
  time_dimension: dimension.transaction-date
  relative_time_anchor: max_available_date
  warnings:
  - 'Proxy only: the database lacks complete bank P&L components required for audited
    Total Operating Income.'
  - Exclude Deposit, Withdrawal, Transfer In, and Transfer Out because they are principal
    or customer cash movements, not operating revenue or expense.
  - Preserve transaction grain when joining transactions to accounts and branches;
    group branches by branch_id and label them with branch_name.
aliases:
- TOI
- TOI by branch
- total operating income by branch
- branch operating income proxy
---

# Branch TOI Proxy

Partial branch operating-income proxy calculated as fee debits less interest credits; it is not audited Total Operating Income.

Formula: `SUM(CASE WHEN transactions.txn_type = 'Fee Debit' THEN transactions.amount WHEN transactions.txn_type = 'Interest Credit' THEN -transactions.amount ELSE 0 END) FROM branches JOIN accounts ON branches.branch_id = accounts.branch_id JOIN transactions ON accounts.account_id = transactions.account_id`
