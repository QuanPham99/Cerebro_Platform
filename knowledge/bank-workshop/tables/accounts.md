---
type: table
id: table.accounts
name: Accounts
description: Bank accounts owned by customers and serviced by branches.
status: active
aliases:
- bank accounts
- account balance
tags:
- banking
- accounts
links:
- dataset.bank-workshop
- relationship.account_customer
- relationship.account_branch
- relationship.transaction_account
- relationship.card_account
- concept.account-balance
- concept.branch-performance
- concept.transaction-activity
- concept.active-customer
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: confidential
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
---

# Accounts

Bank accounts owned by customers and serviced by branches.

Grain: **one row per bank account**.
