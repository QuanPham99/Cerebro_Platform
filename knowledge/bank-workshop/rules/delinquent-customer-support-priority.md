---
type: Business Rule
id: rule.delinquent-customer-support-priority
title: Delinquent Customer Support Priority
description: Prioritize a customer when an open support ticket exists and any loan
  payment is flagged late during the latest 90-day period; join support tickets to
  customers, customers to loans, and loans to loan payments, then evaluate existence
  at customer grain to prevent fanout.
status: stable
links:
- entity.customer
- table.customers
- table.loan_payments
- table.loans
- table.support_tickets
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: business_rule
  classification: restricted
  entity: entity.customer
  rule_kind: classification
  output_type: boolean
  dependencies:
  - table.support_tickets
  - table.customers
  - table.loans
  - table.loan_payments
  logic: Prioritize a customer when an open support ticket exists and any loan payment
    is flagged late during the latest 90-day period; join support tickets to customers,
    customers to loans, and loans to loan payments, then evaluate existence at customer
    grain to prevent fanout.
  grain:
    type: entity
    description: One customer
  warnings: []
---

# Delinquent Customer Support Priority

Prioritize a customer when an open support ticket exists and any loan payment is flagged late during the latest 90-day period; join support tickets to customers, customers to loans, and loans to loan payments, then evaluate existence at customer grain to prevent fanout.
