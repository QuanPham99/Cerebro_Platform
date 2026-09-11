---
type: Business Rule
id: rule.branch-toi-proxy-definition
title: Branch TOI Proxy Definition
description: A branch TOI proxy is compliant only when it equals the sum of Fee Debit
  amounts minus the sum of Interest Credit amounts at transaction grain, using transactions
  to accounts to branches; Deposit, Withdrawal, Transfer In, and Transfer Out must
  contribute zero, and the result must be labeled as a proxy because complete bank
  P&L components are unavailable.
status: stable
links:
- dimension.transaction-type
- entity.branch
- metric.branch-toi-proxy
- table.accounts
- table.branches
- table.transactions
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: business_rule
  classification: confidential
  entity: entity.branch
  rule_kind: aggregation_constraint
  output_type: boolean
  dependencies:
  - metric.branch-toi-proxy
  - dimension.transaction-type
  - table.branches
  - table.accounts
  - table.transactions
  logic: A branch TOI proxy is compliant only when it equals the sum of Fee Debit
    amounts minus the sum of Interest Credit amounts at transaction grain, using transactions
    to accounts to branches; Deposit, Withdrawal, Transfer In, and Transfer Out must
    contribute zero, and the result must be labeled as a proxy because complete bank
    P&L components are unavailable.
  grain:
    type: entity
    description: One branch
  warnings: []
---

# Branch TOI Proxy Definition

A branch TOI proxy is compliant only when it equals the sum of Fee Debit amounts minus the sum of Interest Credit amounts at transaction grain, using transactions to accounts to branches; Deposit, Withdrawal, Transfer In, and Transfer Out must contribute zero, and the result must be labeled as a proxy because complete bank P&L components are unavailable.
