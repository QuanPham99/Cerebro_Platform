---
type: table
id: table.cards
name: Cards
description: Cards issued to customers and linked to bank accounts.
status: active
aliases:
- payment cards
- debit cards
- credit cards
tags:
- banking
- cards
links:
- dataset.bank-workshop
- relationship.card_customer
- relationship.card_account
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
---

# Cards

Cards issued to customers and linked to bank accounts.

Grain: **one row per issued bank card**.
