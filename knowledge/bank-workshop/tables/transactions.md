---
type: Table
id: table.transactions
title: Transactions
description: Account-level money movement with positive unsigned amounts.
status: stable
links:
- dataset.bank-workshop
- relationship.transaction_account
sources:
- id: duckdb-catalog
  resource: DuckDB information_schema
  title: DuckDB catalog metadata
provenance:
  origin: discovered
  source: DuckDB information_schema
cerebro:
  kind: physical_table
  classification: internal
  physical:
    schema: main
    table: transactions
  schema: main
  grain: one account-level ledger event
  primary_key: transaction_id
  columns:
  - name: transaction_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: account_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: txn_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: txn_type
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: amount
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: channel
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: merchant_category
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
resource: duckdb://bank/main/transactions
---

# Transactions

Account-level money movement with positive unsigned amounts.

Grain: **one account-level ledger event**.
