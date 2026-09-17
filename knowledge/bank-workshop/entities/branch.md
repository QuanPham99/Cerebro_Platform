---
type: Entity
id: entity.branch
title: Branch
description: Business entity for branch records.
status: stable
links:
- table.branches
- domain.operations
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
  domain: domain.operations
  physical_mapping:
    table: table.branches
    key:
    - branch_id
  grain:
    type: entity
    description: one row per bank branch
    key:
    - branch_id
  warnings: []
---

# Branch

Branch is represented by one `branches` record at its declared grain.
