---
type: Entity
id: entity.loan-payment
title: Loan Payment
description: Business entity for loan payment records.
aliases:
- "thanh toán khoản vay"
- "kỳ thanh toán"
- "kỳ trả nợ"
status: stable
links:
- table.loan_payments
- domain.lending
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
  domain: domain.lending
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
