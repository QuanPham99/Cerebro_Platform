---
type: table
id: table.loans
name: Loans
description: Customer loans, principal, terms, rates, and performance status.
status: active
aliases:
- credit facilities
- lending
- bad debt
tags:
- banking
- loans
links:
- dataset.bank-workshop
- relationship.loan_customer
- relationship.loan_branch
- relationship.loan_payment_loan
- concept.branch-performance
- concept.repayment-behavior
- concept.bad-debt
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: internal
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
---

# Loans

Customer loans, principal, terms, rates, and performance status.

Grain: **one row per originated loan**.
