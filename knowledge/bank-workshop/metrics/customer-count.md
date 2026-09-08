---
type: Metric
id: metric.customer-count
title: Customer Count
description: Distinct count of banking customers.
status: stable
links:
- dimension.customer-age
- dimension.customer-gender
- entity.customer
- table.customers
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: metric
  classification: restricted
  entity: entity.customer
  measure:
    kind: aggregate
    aggregation: count_distinct
    source:
      table: table.customers
      column: customer_id
    predicates: []
  dependencies:
  - table.customers
  formula: COUNT(DISTINCT customers.customer_id)
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.customer-gender
  - dimension.customer-age
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Return aggregated results for restricted customer attributes.
aliases:
- customer gender population
- customer demographic total
---

# Customer Count

Distinct count of banking customers.

Formula: `COUNT(DISTINCT customers.customer_id)`
