---
type: Relationship
id: relationship.loan_payment_loan
title: Loan Payment Loan
description: Declared join from loan_payments.loan_id to loans.loan_id.
status: stable
links:
- entity.loan
- entity.loan-payment
- table.loan_payments
- table.loans
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: relationship
  classification: internal
  edge_type: physical_fk
  semantic:
    from: entity.loan-payment
    to: entity.loan
  physical:
    source:
      table: table.loan_payments
      column: loan_id
    target:
      table: table.loans
      column: loan_id
  source_table: table.loan_payments
  source_column: loan_id
  target_table: table.loans
  target_column: loan_id
  cardinality: many-to-one
  join_type:
    default: left
  validation:
    target_unique: not_checked
    source_fk_coverage: not_checked
    fanout: not_checked
  warnings:
  - Declared relationship; source-row profiling is disabled.
---

# Loan Payment Loan

Use the declared `many-to-one` join.
