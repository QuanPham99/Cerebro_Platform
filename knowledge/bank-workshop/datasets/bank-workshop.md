---
type: dataset
id: dataset.bank-workshop
name: Bank workshop dataset
description: Synthetic retail banking reference dataset with ten related tables.
status: active
tags:
- banking
- duckdb
- synthetic
links:
- table.accounts
- table.branches
- table.card_transactions
- table.cards
- table.customers
- table.employees
- table.loan_payments
- table.loans
- table.support_tickets
- table.transactions
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
cerebro:
  classification: restricted
  table_count: 10
  column_count: 75
  row_sampling: disabled
---

# Bank workshop dataset

Catalog structure is discovered; keys, relationships, and business rules are declared and reviewed.
