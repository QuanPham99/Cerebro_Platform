---
type: Business Rule
id: rule.loan-maturity-date
title: Loan Maturity Date
description: Loan maturity date is loans.start_date plus loans.term_months months.
aliases:
- "đáo hạn"
- "ngày đáo hạn"
- "hạn khoản vay"
status: stable
links:
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
  kind: business_rule
  classification: confidential
  entity: entity.loan
  rule_kind: time_anchor
  output_type: date
  dependencies:
  - table.loans
  logic: Loan maturity date is loans.start_date plus loans.term_months months.
  grain:
    type: entity
    description: One loan
  warnings: []
---

# Loan Maturity Date

Loan maturity date is loans.start_date plus loans.term_months months.
