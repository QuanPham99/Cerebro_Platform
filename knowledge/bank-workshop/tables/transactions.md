---
type: table
id: table.transactions
name: Transactions
description: Account-level money movement with positive unsigned amounts.
status: active
aliases:
- account activity
- ledger
- deposits
- withdrawals
tags:
- banking
- transactions
links:
- dataset.bank-workshop
- relationship.transaction_account
- concept.branch-performance
- concept.transaction-activity
- concept.active-customer
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: internal
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
  warnings:
  - Amounts are positive; use txn_type for direction.
  - Anchor relative time to MAX(txn_date).
  - Do not UNION raw rows with card_transactions.
---

# Transactions

Account-level money movement with positive unsigned amounts.

Grain: **one account-level ledger event**.
