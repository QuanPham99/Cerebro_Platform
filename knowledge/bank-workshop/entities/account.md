---
type: Entity
id: entity.account
title: Account
description: Business entity for account records.
status: stable
links:
- table.accounts
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
  physical_mapping:
    table: table.accounts
    key:
    - account_id
  grain:
    type: entity
    description: one row per bank account
    key:
    - account_id
  warnings: []
---

# Account

Account is represented by one `accounts` record at its declared grain.
