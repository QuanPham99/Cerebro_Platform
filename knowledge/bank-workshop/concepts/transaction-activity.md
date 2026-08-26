---
type: concept
id: concept.transaction-activity
name: Account transaction activity
description: Unsigned account ledger events whose direction is determined by transaction
  type.
status: active
aliases:
- monthly transaction growth
- transaction volume
- money movement
tags:
- business-concept
- banking
links: &id001
- table.transactions
- table.accounts
- metric.transaction-volume
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: confidential
  warnings:
  - Amounts are positive; derive inflow/outflow from txn_type.
  - Anchor recent periods to MAX(transactions.txn_date).
---

# Account transaction activity

Unsigned account ledger events whose direction is determined by transaction type.
