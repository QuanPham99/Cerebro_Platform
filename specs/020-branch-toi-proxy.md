# 020 — Branch TOI Proxy

## Decision

The bank-workshop database does not contain the complete profit-and-loss components required for
audited Total Operating Income (TOI). It can support a clearly labeled branch-level proxy from the
account transaction ledger.

## Metric Contract

- ID: `metric.branch-toi-proxy`
- Display name: `Branch TOI Proxy`
- Grain: requested aggregate dimensions, with branch identity resolved by `branches.branch_id`
- Formula: fee-debit amount minus interest-credit amount
- Join path: `transactions.account_id -> accounts.account_id -> branches.branch_id`
- Compatible dimensions: branch, transaction type, and transaction date
- Relative periods anchor to `MAX(transactions.txn_date)`
- Result type: decimal; classification: confidential

Deposits, withdrawals, transfers in, and transfers out contribute zero because they represent
principal or customer cash movement rather than operating revenue or expense. The metric must retain
a visible warning that loan interest income, funding costs, trading income, and other P&L components
are unavailable.

## Business Rule Contract

`rule.branch-toi-proxy-definition` is an aggregation constraint linked to the metric, transaction-type
dimension, and its three physical dependencies. A result is compliant only when it applies the exact
signed formula at transaction grain and labels the result as a proxy rather than audited TOI.

## Acceptance

- The default bank-workshop bundle validates with 11 metrics, 11 business rules, and 66 objects.
- The semantic graph exposes the metric-to-table and rule-to-metric dependency edges.
- Grounding for “TOI by branch” retrieves both the metric and its governing rule.
- The bundled DuckDB produces one non-null proxy value for each of its 150 branch IDs.
