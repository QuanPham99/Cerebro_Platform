---
type: policy
id: policy.sensitive-banking-data
name: Sensitive banking data
description: Treat synthetic identity, contact, financial, and credit fields as sensitive.
status: active
aliases:
- PII policy
- restricted data
tags:
- policy
- classification
links: &id001
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
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
cerebro:
  classification: restricted
  applies_to: *id001
  rule: Return aggregate results and minimize restricted fields.
---

# Sensitive banking data

Synthetic data receives the same handling as real restricted banking data.
