---
type: table
id: table.loan_payments
name: Loan Payments
description: Loan repayment events and late-payment outcomes.
status: active
aliases:
- repayments
- late payments
- installments
tags:
- banking
- loan_payments
links:
- dataset.bank-workshop
- relationship.loan_payment_loan
- concept.repayment-behavior
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: internal
  schema: main
  grain: one scheduled or received payment event per loan
  primary_key: payment_id
  columns:
  - name: payment_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: loan_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: payment_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: amount_paid
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: principal_component
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: interest_component
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: late_payment_flag
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
---

# Loan Payments

Loan repayment events and late-payment outcomes.

Grain: **one scheduled or received payment event per loan**.
