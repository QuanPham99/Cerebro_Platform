---
type: Business Rule
id: rule.branch-fraud-escalation
title: Branch Fraud Escalation
description: Escalate a branch when it has at least five fraudulent card transactions
  or at least 10000 in fraudulent card-transaction amount during the latest 30-day
  period; join branches to accounts, accounts to cards, and cards to card transactions
  while preserving card-transaction grain.
status: stable
links:
- entity.branch
- table.accounts
- table.branches
- table.card_transactions
- table.cards
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
  rule_kind: classification
  output_type: boolean
  dependencies:
  - table.branches
  - table.accounts
  - table.cards
  - table.card_transactions
  logic: Escalate a branch when it has at least five fraudulent card transactions
    or at least 10000 in fraudulent card-transaction amount during the latest 30-day
    period; join branches to accounts, accounts to cards, and cards to card transactions
    while preserving card-transaction grain.
  grain:
    type: entity
    description: One branch
  warnings: []
---

# Branch Fraud Escalation

Escalate a branch when it has at least five fraudulent card transactions or at least 10000 in fraudulent card-transaction amount during the latest 30-day period; join branches to accounts, accounts to cards, and cards to card transactions while preserving card-transaction grain.
