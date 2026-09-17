---
type: Table
id: table.cards
title: Cards
description: Cards issued to customers and linked to bank accounts.
aliases:
- "thẻ"
- "danh sách thẻ"
status: stable
links:
- dataset.bank-workshop
- relationship.card_account
- relationship.card_customer
- relationship.card_transaction_card
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
    table: cards
  schema: main
  grain: one row per issued bank card
  primary_key: card_id
  columns:
  - name: card_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: customer_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: account_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: card_type
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: issue_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: expiry_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: credit_limit
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: status
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
resource: duckdb://bank/main/cards
---

# Cards

Cards issued to customers and linked to bank accounts.

Grain: **one row per issued bank card**.
