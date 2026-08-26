---
type: relationship
id: relationship.support_ticket_customer
name: Support Ticket Customer
description: Declared join from support_tickets.customer_id to customers.customer_id.
status: active
tags:
- join
- physical-fk
links:
- table.support_tickets
- table.customers
provenance:
  origin: human_reviewed
  source: config/bank-source.yaml
  database_constraint: false
cerebro:
  classification: internal
  edge_type: physical_fk
  source_table: table.support_tickets
  source_column: customer_id
  target_table: table.customers
  target_column: customer_id
  cardinality: many-to-one
  warnings:
  - Declared relationship; the source DuckDB does not define FK constraints.
---

# Support Ticket Customer

Use an exact ID join with `many-to-one` cardinality.
