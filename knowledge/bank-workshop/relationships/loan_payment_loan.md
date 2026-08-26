---
type: relationship
id: relationship.loan_payment_loan
name: Loan Payment Loan
description: Declared join from loan_payments.loan_id to loans.loan_id.
status: active
tags:
- join
- physical-fk
links:
- table.loan_payments
- table.loans
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.loan_payments
  source_column: loan_id
  target_table: table.loans
  target_column: loan_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Loan Payment Loan

Use an exact ID join with `many-to-one` cardinality.
