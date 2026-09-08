---
type: Entity
id: entity.loan-payment
title: Loan Payment
description: Business entity for loan payment records.
status: stable
links:
- table.loan_payments
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: entity
  classification: internal
  physical_mapping:
    table: table.loan_payments
    key:
    - payment_id
  grain:
    type: event
    description: one scheduled or received payment event per loan
    key:
    - payment_id
  warnings: []
---

# Loan Payment

Loan Payment is represented by one `loan_payments` record at its declared grain.
