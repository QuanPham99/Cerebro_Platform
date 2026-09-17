---
type: Metric
id: metric.card-transaction-amount-total
title: Card Purchase Amount
description: Sum of a card's own purchase/withdrawal amounts recorded in card_transactions,
  distinct from generic account-level transactions.
aliases:
- "tổng số tiền giao dịch thẻ"
- "tổng chi tiêu thẻ"
- "giao dịch thẻ số tiền lớn nhất"
status: stable
links:
- dimension.card-type
- dimension.merchant-category
- entity.card-transaction
- table.card_transactions
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
  entity: entity.card-transaction
  measure:
    kind: aggregate
    aggregation: sum
    source:
      table: table.card_transactions
      column: amount
    predicates: []
  dependencies:
  - table.card_transactions
  formula: SUM(card_transactions.amount)
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.card-type
  - dimension.merchant-category
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Card purchase/withdrawal amounts live only in card_transactions (joined via
    cards.card_id); table.transactions has no card channel or txn_type value and
    never represents a card purchase, so any question about card transaction
    amounts — including "largest"/"total" card amounts — must read amount from
    card_transactions, never by filtering transactions.channel or
    transactions.txn_type for "card".
---

# Card Purchase Amount

Sum of a card's own purchase/withdrawal amounts recorded in card_transactions.

Formula: `SUM(card_transactions.amount)`
