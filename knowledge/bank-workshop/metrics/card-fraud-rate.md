---
type: metric
id: metric.card-fraud-rate
name: Card fraud rate
description: Percentage of card transactions flagged as fraud.
status: active
aliases:
- fraud percent
- fraud rate by card type
tags:
- metric
- banking
links: &id001
- table.card_transactions
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  classification: confidential
  dependencies: *id001
  formula: 100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0)
  metric_result_type: decimal
  filters: []
  grain: aggregate over card transaction events
  warnings:
  - Safe division returns NULL for an empty population.
---

# Card fraud rate

Percentage of card transactions flagged as fraud.

Formula: `100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0)`
