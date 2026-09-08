---
type: Table
id: table.card_transactions
title: Card Transactions
description: Card-level payment activity with fraud outcomes.
status: stable
links:
- dataset.bank-workshop
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
    table: card_transactions
  schema: main
  grain: one purchase or withdrawal event per card
  primary_key: card_txn_id
  columns:
  - name: card_txn_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: card_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: txn_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: merchant_category
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: amount
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: is_fraud
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
resource: duckdb://bank/main/card_transactions
---

# Card Transactions

Card-level payment activity with fraud outcomes.

Grain: **one purchase or withdrawal event per card**.
