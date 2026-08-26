---
type: metric
id: metric.non-performing-loan-rate
name: Non-performing loan rate
description: Percentage of loans that are Defaulted or Written Off.
status: active
aliases:
- bad debt rate
- default rate
- NPL rate
tags:
- metric
- banking
links: &id001
- table.loans
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  classification: confidential
  dependencies: *id001
  formula: 100.0 * SUM(CASE WHEN loans.status IN ('Defaulted', 'Written Off') THEN
    1 ELSE 0 END) / NULLIF(COUNT(*), 0)
  filters: []
  grain: aggregate over originated loans
  warnings:
  - Safe division returns NULL for an empty population.
---

# Non-performing loan rate

Percentage of loans that are Defaulted or Written Off.

Formula: `100.0 * SUM(CASE WHEN loans.status IN ('Defaulted', 'Written Off') THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)`
