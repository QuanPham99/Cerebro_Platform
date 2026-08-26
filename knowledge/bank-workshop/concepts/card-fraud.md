---
type: concept
id: concept.card-fraud
name: Card fraud
description: Fraud incidence across card transactions, card types, customers, and
  accounts.
status: active
aliases:
- fraud rate
- fraud percentage
- card fraud percent
- is_fraud
tags:
- business-concept
- banking
links: &id001
- table.card_transactions
- table.cards
- metric.card-fraud-rate
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: confidential
  warnings:
  - Use card transaction grain; do not mix directly with account transactions.
---

# Card fraud

Fraud incidence across card transactions, card types, customers, and accounts.
