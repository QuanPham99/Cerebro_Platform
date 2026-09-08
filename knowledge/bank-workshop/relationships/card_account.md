---
type: Relationship
id: relationship.card_account
title: Card Account
description: Declared join from cards.account_id to accounts.account_id.
status: stable
links:
- entity.account
- entity.card
- table.accounts
- table.cards
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: relationship
  classification: internal
  edge_type: physical_fk
  semantic:
    from: entity.card
    to: entity.account
  physical:
    source:
      table: table.cards
      column: account_id
    target:
      table: table.accounts
      column: account_id
  source_table: table.cards
  source_column: account_id
  target_table: table.accounts
  target_column: account_id
  cardinality: many-to-one
  join_type:
    default: left
  validation:
    target_unique: not_checked
    source_fk_coverage: not_checked
    fanout: not_checked
  warnings:
  - Declared relationship; source-row profiling is disabled.
---

# Card Account

Use the declared `many-to-one` join.
