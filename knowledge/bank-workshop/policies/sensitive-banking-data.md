---
type: Policy
id: policy.sensitive-banking-data
title: Sensitive Banking Data
description: Treat synthetic identity, contact, financial, and credit fields as sensitive.
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
  kind: policy
  classification: restricted
  applies_to:
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
  rule: Return aggregate results and minimize restricted fields.
---

# Sensitive Banking Data

Synthetic data receives the same handling as real restricted banking data.
