---
type: Dataset
id: dataset.bank-workshop
title: Bank Workshop Dataset
description: Synthetic retail-banking reference dataset with ten related tables.
status: stable
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
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: dataset
  classification: restricted
  table_count: 10
  column_count: 75
  row_sampling: disabled
---

# Bank Workshop Dataset

Catalog structure is discovered; semantic definitions are reviewed.
