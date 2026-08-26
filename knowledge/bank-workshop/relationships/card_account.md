---
type: relationship
id: relationship.card_account
name: Card Account
description: Declared join from cards.account_id to accounts.account_id.
status: active
tags:
- join
- physical-fk
links:
- table.cards
- table.accounts
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.cards
  source_column: account_id
  target_table: table.accounts
  target_column: account_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Card Account

Use an exact ID join with `many-to-one` cardinality.
