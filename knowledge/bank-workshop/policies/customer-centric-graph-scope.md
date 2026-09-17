---
type: Policy
id: policy.customer-centric-graph-scope
title: Customer-Centric Graph Scope
description: Define the object-level allowlist the customer self-service workspace may ever ground on or render.
status: stable
links:
- table.customers
- table.accounts
- table.cards
- table.card_transactions
- table.transactions
- table.loans
- table.loan_payments
sources:
- id: semantic-definition
  resource: docs/semantic-layer-definition.md
  title: Cerebro semantic-layer definition
provenance:
  origin: human_reviewed
  source: docs/semantic-layer-definition.md
cerebro:
  kind: policy
  classification: restricted
  applies_to:
  - table.customers
  - table.accounts
  - table.cards
  - table.card_transactions
  - table.transactions
  - table.loans
  - table.loan_payments
  rule: >-
    The customer self-service graph and its grounding may only include entity.customer,
    entity.account, entity.card, entity.card-transaction, entity.transaction, entity.loan,
    entity.loan-payment and their direct relationships, dimensions, and row-level metrics.
    entity.branch and entity.employee, plus every population-level risk or fraud metric and
    internal-ops rule, are excluded regardless of which customer is asking.
---

# Customer-Centric Graph Scope

The full Semantic Constellation graph documents the bank's entire operating model, including
branch/employee structure and population-level risk metrics (fraud exposure, non-performing-loan
rate, TOI proxy). None of that is appropriate to show a retail customer, even about themselves.

The tables this policy applies to (`applies_to`, above) are the seven customer-owned physical
tables in scope: `customers`, `accounts`, `cards`, `card_transactions`, `transactions`, `loans`,
`loan_payments`. The object-level exclusions this policy documents are semantic (entity/metric/
rule) objects rather than tables, so they are listed here in prose instead of `applies_to` — the
bundle schema restricts a policy's `applies_to` to physical-table targets only. Excluded:
`entity.branch`, `entity.employee`, `entity.support-ticket`; `metric.branch-fraud-exposure`,
`metric.branch-toi-proxy`, `metric.card-fraud-rate`, `metric.customer-count`,
`metric.late-payment-rate`, `metric.non-performing-loan-rate`,
`metric.supported-delinquency-population`; `rule.branch-fraud-escalation`,
`rule.employee-risk-portfolio-assignment`, `rule.delinquent-customer-support-priority`,
`rule.high-value-multichannel-customer`, `rule.branch-toi-proxy-definition`,
`rule.non-performing-loan`.

`GET /api/graph?scope=customer` (and the customer workspace's embedded graph view) project the
full graph down to the entities, relationships, dimensions, metrics, and rules a customer may see
about their own accounts, cards, loans, and transactions. A customer may see a row-level fact
about their own data (e.g. whether one of their own card transactions was flagged as fraud, via
`rule.fraudulent-card-transaction`) but never an aggregate or cross-customer signal.

This scope is the same object-id allowlist enforced when grounding the customer workspace's chat
agent (`src/cerebro/customer_scope.py:CUSTOMER_SCOPE_OBJECT_IDS`) — the graph and the agent must
never disagree about what a customer is allowed to see.
