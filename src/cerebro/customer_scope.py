"""Customer self-service scope: the fixed allowlist of semantic objects and physical
tables a retail customer may ever see or query, and the deterministic row-level filter
that scopes every query to a single customer's own rows.

This intentionally excludes bank-internal entities (branches, employees) and
population-level risk/fraud metrics — a customer may see facts about their own rows
(e.g. whether their own card transaction was flagged as fraud) but never aggregate or
cross-customer signals such as ``branch-fraud-exposure`` or ``non-performing-loan-rate``.
"""

from __future__ import annotations

from .graph_projection import project_graph
from .models import GraphResponse

CUSTOMER_SCOPE_DOMAIN_IDS: frozenset[str] = frozenset({
    "domain.retail-banking",
    "domain.lending",
    "domain.cards",
})

CUSTOMER_SCOPE_ENTITY_IDS: frozenset[str] = frozenset({
    "entity.customer",
    "entity.account",
    "entity.card",
    "entity.card-transaction",
    "entity.transaction",
    "entity.loan",
    "entity.loan-payment",
})

CUSTOMER_SCOPE_RELATIONSHIP_IDS: frozenset[str] = frozenset({
    "relationship.account_customer",
    "relationship.card_customer",
    "relationship.loan_customer",
    "relationship.transaction_account",
    "relationship.card_account",
    "relationship.card_transaction_card",
    "relationship.loan_payment_loan",
})

CUSTOMER_SCOPE_DIMENSION_IDS: frozenset[str] = frozenset({
    "dimension.account-type",
    "dimension.card-type",
    "dimension.loan-status",
    "dimension.loan-type",
    "dimension.merchant-category",
    "dimension.transaction-channel",
    "dimension.transaction-date",
    "dimension.transaction-type",
})

CUSTOMER_SCOPE_METRIC_IDS: frozenset[str] = frozenset({
    "metric.account-balance",
    "metric.transaction-volume",
    "metric.customer-net-cash-flow",
    "metric.customer-loan-repayment-total",
    "metric.customer-loan-principal-paid-total",
    "metric.card-transaction-amount-total",
})

CUSTOMER_SCOPE_RULE_IDS: frozenset[str] = frozenset({
    "rule.active-customer",
    "rule.late-loan-payment",
    "rule.transaction-direction",
    "rule.relative-time-anchor",
    "rule.fraudulent-card-transaction",
    "rule.loan-maturity-date",
})

CUSTOMER_SCOPE_POLICY_IDS: frozenset[str] = frozenset({
    "policy.sensitive-banking-data",
    "policy.customer-centric-graph-scope",
    "policy.customer-self-service-row-level-security",
})

CUSTOMER_SCOPE_TABLE_IDS: frozenset[str] = frozenset({
    "table.customers",
    "table.accounts",
    "table.cards",
    "table.card_transactions",
    "table.transactions",
    "table.loans",
    "table.loan_payments",
})

# Everything a customer-scoped grounding/graph request may reference. Deliberately
# excludes entity.branch, entity.employee, entity.support-ticket and every
# population-level risk/fraud metric (branch-fraud-exposure, branch-toi-proxy,
# card-fraud-rate, non-performing-loan-rate, late-payment-rate,
# supported-delinquency-population, customer-count) and internal-ops rules
# (branch-fraud-escalation, employee-risk-portfolio-assignment,
# delinquent-customer-support-priority, high-value-multichannel-customer,
# branch-toi-proxy-definition, non-performing-loan).
CUSTOMER_SCOPE_OBJECT_IDS: frozenset[str] = (
    CUSTOMER_SCOPE_DOMAIN_IDS
    | CUSTOMER_SCOPE_ENTITY_IDS
    | CUSTOMER_SCOPE_RELATIONSHIP_IDS
    | CUSTOMER_SCOPE_DIMENSION_IDS
    | CUSTOMER_SCOPE_METRIC_IDS
    | CUSTOMER_SCOPE_RULE_IDS
    | CUSTOMER_SCOPE_POLICY_IDS
    | CUSTOMER_SCOPE_TABLE_IDS
)

# Physical tables a customer-workspace query may never touch, regardless of what the
# proposed SQL asks for. Enforced in SQLGuardrail, independent of the object-id
# allowlist above (which only gates grounding/graph visibility, not query execution).
CUSTOMER_SCOPE_BLOCKED_TABLES: frozenset[str] = frozenset({"branches", "employees", "support_tickets"})

# Table -> the row-level filter that scopes that table to a single customer's own rows,
# expressed as the WHERE-clause body a filtered derived table is built from. Applied by
# SQLGuardrail.validate() before any join/aggregation, so it is the actual security
# boundary — not the object-id allowlist, which only shapes what the LLM sees.
CUSTOMER_ROW_FILTER_TABLES: dict[str, str] = {
    "customers": "customer_id = :cid",
    "accounts": "customer_id = :cid",
    "loans": "customer_id = :cid",
    "cards": "customer_id = :cid",
    "transactions": "account_id IN (SELECT account_id FROM accounts WHERE customer_id = :cid)",
    "card_transactions": "card_id IN (SELECT card_id FROM cards WHERE customer_id = :cid)",
    "loan_payments": "loan_id IN (SELECT loan_id FROM loans WHERE customer_id = :cid)",
}


def filter_graph_for_customer_scope(graph: GraphResponse) -> GraphResponse:
    """Project a full semantic graph down to the customer self-service allowlist.

    Used by ``GET /api/graph?scope=customer`` so the browser never receives the full
    graph for this view — nodes outside :data:`CUSTOMER_SCOPE_OBJECT_IDS` are dropped,
    and only edges whose endpoints are both still present survive.
    """
    return project_graph(graph, set(CUSTOMER_SCOPE_OBJECT_IDS))
