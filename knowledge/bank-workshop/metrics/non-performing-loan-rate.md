---
type: Metric
id: metric.non-performing-loan-rate
title: Non-performing Loan Rate
description: Percentage of loans with Defaulted or Written Off status.
status: stable
links:
- dimension.branch
- dimension.loan-status
- dimension.loan-type
- entity.loan
- table.loans
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: metric
  classification: confidential
  entity: entity.loan
  measure:
    kind: ratio
    numerator:
      kind: aggregate
      aggregation: count
      source: null
      predicates:
      - source:
          table: table.loans
          column: status
        operator: in
        value:
        - Defaulted
        - Written Off
    denominator:
      kind: aggregate
      aggregation: count
      source: null
      predicates: []
    scale: 100.0
  dependencies:
  - table.loans
  formula: 100 * SUM(CASE WHEN loans.status IN ('Defaulted', 'Written Off') THEN 1
    ELSE 0 END) / NULLIF(COUNT(*), 0)
  metric_result_type: decimal
  filters: []
  grain:
    type: aggregate
    description: Requested compatible dimensions
  compatible_dimensions:
  - dimension.loan-type
  - dimension.loan-status
  - dimension.branch
  time_dimension: null
  relative_time_anchor: null
  warnings:
  - Use originated-loan grain.
---

# Non-performing Loan Rate

Percentage of loans with Defaulted or Written Off status.

Formula: `100 * SUM(CASE WHEN loans.status IN ('Defaulted', 'Written Off') THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)`
