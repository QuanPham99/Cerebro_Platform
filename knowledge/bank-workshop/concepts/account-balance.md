---
type: concept
id: concept.account-balance
name: Account balance
description: Current account balance analyzed by account type, status, customer, or
  branch.
status: active
aliases:
- average balance
- avg account balance
- deposits by account type
tags:
- business-concept
- banking
links: &id001
- table.accounts
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: confidential
  warnings:
  - Balance is a current snapshot, not a transaction flow.
---

# Account balance

Current account balance analyzed by account type, status, customer, or branch.
