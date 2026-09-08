---
type: Table
id: table.support_tickets
title: Support Tickets
description: Customer issues, status, resolution dates, and satisfaction.
status: stable
links:
- dataset.bank-workshop
- relationship.support_ticket_customer
sources:
- id: duckdb-catalog
  resource: DuckDB information_schema
  title: DuckDB catalog metadata
provenance:
  origin: discovered
  source: DuckDB information_schema
cerebro:
  kind: physical_table
  classification: internal
  physical:
    schema: main
    table: support_tickets
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
resource: duckdb://bank/main/support_tickets
---

# Support Tickets

Customer issues, status, resolution dates, and satisfaction.

Grain: **one row per customer support case**.
