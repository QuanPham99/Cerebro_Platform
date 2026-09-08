---
type: Table
id: table.loans
title: Loans
description: Customer loans, principal, terms, rates, and performance status.
status: stable
links:
- dataset.bank-workshop
- relationship.loan_branch
- relationship.loan_customer
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
    table: loans
  schema: main
  grain: one row per originated loan
  primary_key: loan_id
  columns:
  - name: loan_id
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
  - name: loan_type
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: loan_amount
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: interest_rate
    data_type: DOUBLE
    nullable: true
    classification: internal
    provenance: discovered
  - name: term_months
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: start_date
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
resource: duckdb://bank/main/loans
---

# Loans

Customer loans, principal, terms, rates, and performance status.

Grain: **one row per originated loan**.
