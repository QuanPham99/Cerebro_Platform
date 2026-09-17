---
type: Entity
id: entity.loan
title: Loan
description: Business entity for loan records.
status: stable
links:
- table.loans
- domain.lending
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
  domain: domain.lending
  physical_mapping:
    table: table.loans
    key:
    - loan_id
  grain:
    type: entity
    description: one row per originated loan
    key:
    - loan_id
  warnings: []
---

# Loan

Loan is represented by one `loans` record at its declared grain.
