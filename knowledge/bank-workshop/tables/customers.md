---
type: table
id: table.customers
name: Customers
description: Customer profile, demographics, income, and credit attributes.
status: active
aliases:
- clients
- account holders
- customer demographics
tags:
- banking
- customers
links:
- dataset.bank-workshop
- relationship.account_customer
- relationship.card_customer
- relationship.loan_customer
- relationship.support_ticket_customer
- concept.customer-demographics
- concept.support-workload
- concept.active-customer
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: restricted
  schema: main
  grain: one row per bank customer
  primary_key: customer_id
  columns:
  - name: customer_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: name
    data_type: VARCHAR
    nullable: true
    classification: restricted
    provenance: discovered
  - name: gender
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: date_of_birth
    data_type: DATE
    nullable: true
    classification: restricted
    provenance: discovered
  - name: city
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: state
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: phone
    data_type: BIGINT
    nullable: true
    classification: restricted
    provenance: discovered
  - name: email
    data_type: VARCHAR
    nullable: true
    classification: restricted
    provenance: discovered
  - name: occupation
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: annual_income
    data_type: BIGINT
    nullable: true
    classification: confidential
    provenance: discovered
  - name: join_date
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: credit_score
    data_type: BIGINT
    nullable: true
    classification: confidential
    provenance: discovered
  warnings: []
---

# Customers

Customer profile, demographics, income, and credit attributes.

Grain: **one row per bank customer**.
