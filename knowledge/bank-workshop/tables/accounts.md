---
type: Table
id: table.accounts
title: Accounts
description: Bank accounts owned by customers and serviced by branches.
aliases:
- "tài khoản"
- "danh sách tài khoản"
status: stable
links:
- dataset.bank-workshop
- relationship.account_branch
- relationship.account_customer
- relationship.card_account
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
  classification: confidential
  physical:
    schema: main
    table: accounts
  schema: main
  grain: one row per bank account
  primary_key: account_id
  columns:
  - name: account_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: customer_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: branch_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: account_type
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: balance
    data_type: DOUBLE
    nullable: true
    classification: confidential
    provenance: discovered
  - name: open_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: status
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
resource: duckdb://bank/main/accounts
---

# Accounts

Bank accounts owned by customers and serviced by branches.

Grain: **one row per bank account**.
