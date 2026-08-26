---
type: concept
id: concept.branch-performance
name: Branch performance
description: Transaction amount, lending outcomes, and staffing analyzed by branch.
status: active
aliases:
- branch transaction amount
- branch lending
- branch comparison
tags:
- business-concept
- banking
links: &id001
- table.branches
- table.accounts
- table.transactions
- table.loans
- table.employees
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: internal
  warnings:
  - Account transactions reach branches through accounts; use both declared joins.
---

# Branch performance

Transaction amount, lending outcomes, and staffing analyzed by branch.
