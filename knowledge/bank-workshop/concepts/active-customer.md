---
type: concept
id: concept.active-customer
name: Active customer
description: Customer activity derived separately from account and card logs before
  customer-level combination.
status: active
aliases:
- top active customers
- top 5 percent customers
- account and card activity
tags:
- business-concept
- banking
links: &id001
- table.customers
- table.accounts
- table.transactions
- table.cards
- table.card_transactions
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: restricted
  warnings:
  - Aggregate each activity log to customer grain before combining; never use a naive
    UNION of raw events.
---

# Active customer

Customer activity derived separately from account and card logs before customer-level combination.
