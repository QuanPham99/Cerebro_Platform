---
type: concept
id: concept.bad-debt
name: Bad debt
description: Loans with Defaulted or Written Off status compared with total originated
  loans.
status: active
aliases:
- bad debt rate
- non performing loan
- defaulted written off
tags:
- business-concept
- banking
links: &id001
- table.loans
- table.branches
- table.employees
- metric.non-performing-loan-rate
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  maps_to: *id001
  classification: confidential
  warnings:
  - Loan Officer headcount and loans must each aggregate to branch grain before comparison.
---

# Bad debt

Loans with Defaulted or Written Off status compared with total originated loans.
