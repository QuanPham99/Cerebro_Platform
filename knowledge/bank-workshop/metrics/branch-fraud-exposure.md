---
type: Metric
id: metric.branch-fraud-exposure
title: Branch Fraud Exposure
description: Total fraudulent card-transaction amount attributed to the account's
  servicing branch.
status: stable
links:
- dimension.branch
- dimension.card-type
- dimension.merchant-category
- entity.branch
- table.accounts
- table.branches
- table.card_transactions
- table.cards
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
      table: table.card_transactions
      column: amount
    predicates:
    - source:
        table: table.card_transactions
        column: is_fraud
      operator: eq
      value: 1
  dependencies:
  - table.branches
  - table.accounts
  - table.cards
  - table.card_transactions
  formula: SUM(CASE WHEN card_transactions.is_fraud = 1 THEN card_transactions.amount
    ELSE 0 END) FROM branches JOIN accounts ON branches.branch_id = accounts.branch_id
    JOIN cards ON accounts.account_id = cards.account_id JOIN card_transactions ON
    cards.card_id = card_transactions.card_id
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.branch
  - dimension.card-type
  - dimension.merchant-category
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Preserve card-transaction grain across the three many-to-one joins.
---

# Branch Fraud Exposure

Total fraudulent card-transaction amount attributed to the account's servicing branch.

Formula: `SUM(CASE WHEN card_transactions.is_fraud = 1 THEN card_transactions.amount ELSE 0 END) FROM branches JOIN accounts ON branches.branch_id = accounts.branch_id JOIN cards ON accounts.account_id = cards.account_id JOIN card_transactions ON cards.card_id = card_transactions.card_id`
