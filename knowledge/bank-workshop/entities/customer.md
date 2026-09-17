---
type: Entity
id: entity.customer
title: Customer
description: Business entity for customer records.
status: stable
links:
- table.customers
- domain.retail-banking
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: entity
  classification: restricted
  domain: domain.retail-banking
  physical_mapping:
    table: table.customers
    key:
    - customer_id
  grain:
    type: entity
    description: one row per bank customer
    key:
    - customer_id
  warnings: []
---

# Customer

Customer is represented by one `customers` record at its declared grain.
