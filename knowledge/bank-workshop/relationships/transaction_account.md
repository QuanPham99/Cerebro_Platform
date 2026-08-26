---
type: relationship
id: relationship.transaction_account
name: Transaction Account
description: Declared join from transactions.account_id to accounts.account_id.
status: active
tags:
- join
- physical-fk
links:
- table.transactions
- table.accounts
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.transactions
  source_column: account_id
  target_table: table.accounts
  target_column: account_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Transaction Account

Use an exact ID join with `many-to-one` cardinality.
