---
type: Business Rule
id: rule.active-customer
title: Active Customer
description: Aggregate account and card activity independently to customer grain,
  combine the aggregates, and never union raw event rows.
aliases:
- "đang hoạt động"
- "còn hoạt động"
- "khách hàng đang hoạt động"
status: stable
links:
- entity.card-transaction
- entity.customer
- entity.transaction
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: business_rule
  classification: restricted
  entity: entity.customer
  rule_kind: classification
  output_type: boolean
  dependencies:
  - entity.transaction
  - entity.card-transaction
  logic: Aggregate account and card activity independently to customer grain, combine
    the aggregates, and never union raw event rows.
  grain:
    type: entity
    description: One customer
  warnings: []
---

# Active Customer

Aggregate account and card activity independently to customer grain, combine the aggregates, and never union raw event rows.
