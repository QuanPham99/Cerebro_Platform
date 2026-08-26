---
type: table
id: table.support_tickets
name: Support Tickets
description: Customer issues, status, resolution dates, and satisfaction.
status: active
aliases:
- cases
- customer service
- open issues
tags:
- banking
- support_tickets
links:
- dataset.bank-workshop
- relationship.support_ticket_customer
- concept.support-workload
- policy.sensitive-banking-data
provenance:
  origin: human_reviewed
  catalog: DuckDB information_schema
  semantics: config/bank-source.yaml
cerebro:
  classification: internal
  schema: main
  grain: one row per customer support case
  primary_key: ticket_id
  columns:
  - name: ticket_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: customer_id
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  - name: issue_type
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: date_opened
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: date_resolved
    data_type: DATE
    nullable: true
    classification: internal
    provenance: discovered
  - name: status
    data_type: VARCHAR
    nullable: true
    classification: internal
    provenance: discovered
  - name: satisfaction_score
    data_type: BIGINT
    nullable: true
    classification: internal
    provenance: discovered
  warnings: []
---

# Support Tickets

Customer issues, status, resolution dates, and satisfaction.

Grain: **one row per customer support case**.
