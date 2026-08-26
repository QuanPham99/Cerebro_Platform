---
type: table
id: table.card_transactions
name: Card Transactions
description: Card-level payment activity with fraud outcomes.
status: active
aliases:
- card activity
- card payments
- fraud transactions
tags:
- banking
- card_transactions
links:
- dataset.bank-workshop
- relationship.card_transaction_card
- concept.card-fraud
- concept.active-customer
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: internal
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
  warnings:
  - Card-event grain differs from account transactions.
  - Anchor relative time to MAX(txn_date).
---

# Card Transactions

Card-level payment activity with fraud outcomes.

Grain: **one purchase or withdrawal event per card**.
