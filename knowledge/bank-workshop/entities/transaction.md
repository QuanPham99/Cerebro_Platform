---
type: Entity
id: entity.transaction
title: Transaction
description: Business entity for transaction records.
aliases:
- "giao dịch"
- "giao dịch tài khoản"
status: stable
links:
- table.transactions
- domain.retail-banking
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: entity
  classification: internal
  domain: domain.retail-banking
  physical_mapping:
    table: table.transactions
    key:
    - transaction_id
  grain:
    type: event
    description: one account-level ledger event
    key:
    - transaction_id
  warnings: []
---

# Transaction

Transaction is represented by one `transactions` record at its declared grain.
