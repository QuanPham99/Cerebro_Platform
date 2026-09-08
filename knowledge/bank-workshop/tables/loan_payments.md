---
type: Table
id: table.loan_payments
title: Loan Payments
description: Loan repayment events and late-payment outcomes.
status: stable
links:
- dataset.bank-workshop
- relationship.loan_payment_loan
sources:
- id: duckdb-catalog
  resource: DuckDB information_schema
  title: DuckDB catalog metadata
provenance:
  origin: discovered
  source: DuckDB information_schema
cerebro:
  kind: physical_table
  classification: internal
  physical:
    schema: main
    table: loan_payments
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
resource: duckdb://bank/main/loan_payments
---

# Loan Payments

Loan repayment events and late-payment outcomes.

Grain: **one scheduled or received payment event per loan**.
