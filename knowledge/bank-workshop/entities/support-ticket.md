---
type: Entity
id: entity.support-ticket
title: Support Ticket
description: Business entity for support ticket records.
status: stable
links:
- table.support_tickets
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
    table: table.support_tickets
    key:
    - ticket_id
  grain:
    type: entity
    description: one row per customer support case
    key:
    - ticket_id
  warnings: []
---

# Support Ticket

Support Ticket is represented by one `support_tickets` record at its declared grain.
