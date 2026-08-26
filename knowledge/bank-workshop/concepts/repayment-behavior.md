---
type: concept
id: concept.repayment-behavior
name: Loan repayment behavior
description: Late-payment incidence and payment composition by loan and loan type.
status: active
aliases:
- late payment rate
- repayment delinquency
- loan installments
tags:
- business-concept
- banking
links: &id001
- table.loan_payments
- table.loans
- metric.late-payment-rate
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: confidential
  warnings:
  - Payment events and loans have different grains; aggregate before comparing loan
    types.
---

# Loan repayment behavior

Late-payment incidence and payment composition by loan and loan type.
