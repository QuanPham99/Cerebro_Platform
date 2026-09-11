"""Build the reviewed bank-workshop Semantic Profile v0.1 bundle."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from cerebro.paths import DEFAULT_BUNDLE, DEFAULT_CONFIG
from cerebro.semantic.compiler import metric_formula
from cerebro.source import DuckDBSource
from cerebro.upstream import OKFDocument


ENTITY_NAMES = {
    "customers": "Customer", "accounts": "Account", "transactions": "Transaction",
    "cards": "Card", "card_transactions": "Card Transaction", "loans": "Loan",
    "loan_payments": "Loan Payment", "branches": "Branch", "employees": "Employee",
    "support_tickets": "Support Ticket",
}

SPECIAL_ENTITY_IDS = {
    "card_transactions": "entity.card-transaction", "loan_payments": "entity.loan-payment",
    "support_tickets": "entity.support-ticket", "branches": "entity.branch",
}

DIMENSIONS = [
    ("customer-gender", "Customer Gender", "customer", [("customers", "gender")], "categorical", ["customer-count", "customer-net-cash-flow", "customer-loan-repayment-total", "supported-delinquency-population"], None),
    ("customer-age", "Customer Age", "customer", [("customers", "date_of_birth")], "derived", ["customer-count", "customer-net-cash-flow", "customer-loan-repayment-total", "supported-delinquency-population"], "Completed years from date_of_birth at the relevant maximum available date."),
    ("account-type", "Account Type", "account", [("accounts", "account_type")], "categorical", ["account-balance"], None),
    ("branch", "Branch", "branch", [("branches", "branch_name")], "geographic", ["transaction-volume", "account-balance", "non-performing-loan-rate", "customer-net-cash-flow", "branch-fraud-exposure", "branch-toi-proxy", "customer-loan-repayment-total", "supported-delinquency-population"], None),
    ("transaction-type", "Transaction Type", "transaction", [("transactions", "txn_type")], "categorical", ["transaction-volume", "customer-net-cash-flow", "branch-toi-proxy"], None),
    ("transaction-channel", "Transaction Channel", "transaction", [("transactions", "channel")], "categorical", ["transaction-volume", "customer-net-cash-flow"], None),
    ("merchant-category", "Merchant Category", "card-transaction", [("card_transactions", "merchant_category")], "categorical", ["card-fraud-rate", "branch-fraud-exposure"], None),
    ("card-type", "Card Type", "card", [("cards", "card_type")], "categorical", ["card-fraud-rate", "branch-fraud-exposure"], None),
    ("loan-type", "Loan Type", "loan", [("loans", "loan_type")], "categorical", ["late-payment-rate", "non-performing-loan-rate", "customer-loan-repayment-total", "supported-delinquency-population"], None),
    ("loan-status", "Loan Status", "loan", [("loans", "status")], "categorical", ["non-performing-loan-rate", "customer-loan-repayment-total", "supported-delinquency-population"], None),
    ("transaction-date", "Transaction Date", "transaction", [("transactions", "txn_date")], "temporal", ["transaction-volume", "customer-net-cash-flow", "branch-toi-proxy"], None),
]

METRICS: list[dict[str, Any]] = [
    {
        "id": "transaction-volume", "name": "Transaction Volume", "entity": "transaction",
        "description": "Total positive account transaction amount for a defined period and scope.",
        "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "table.transactions", "column": "amount"}, "predicates": []},
        "dependencies": ["table.transactions"],
        "dimensions": ["dimension.transaction-type", "dimension.transaction-channel", "dimension.transaction-date", "dimension.branch"],
        "time_dimension": "dimension.transaction-date", "relative_time_anchor": "max_available_date",
        "classification": "confidential", "warnings": ["Direction requires transaction type; amount itself is unsigned."],
    },
    {
        "id": "account-balance", "name": "Account Balance", "entity": "account",
        "description": "Average current account balance within the requested dimensional scope.",
        "measure": {"kind": "aggregate", "aggregation": "avg", "source": {"table": "table.accounts", "column": "balance"}, "predicates": []},
        "dependencies": ["table.accounts"], "dimensions": ["dimension.account-type", "dimension.branch"],
        "classification": "confidential", "warnings": ["Balance is a current snapshot, not a transaction flow."],
    },
    {
        "id": "customer-count", "name": "Customer Count", "entity": "customer",
        "description": "Distinct count of banking customers.",
        "aliases": ["customer gender population", "customer demographic total"],
        "result_type": "integer",
        "measure": {"kind": "aggregate", "aggregation": "count_distinct", "source": {"table": "table.customers", "column": "customer_id"}, "predicates": []},
        "dependencies": ["table.customers"], "dimensions": ["dimension.customer-gender", "dimension.customer-age"],
        "classification": "restricted", "warnings": ["Return aggregated results for restricted customer attributes."],
    },
    {
        "id": "card-fraud-rate", "name": "Card Fraud Rate", "entity": "card-transaction",
        "description": "Percentage of card transactions flagged as fraud.",
        "measure": {"kind": "ratio", "numerator": {"kind": "aggregate", "aggregation": "count", "source": None, "predicates": [{"source": {"table": "table.card_transactions", "column": "is_fraud"}, "operator": "eq", "value": 1}]}, "denominator": {"kind": "aggregate", "aggregation": "count", "source": None, "predicates": []}, "scale": 100.0},
        "dependencies": ["table.card_transactions"], "dimensions": ["dimension.merchant-category", "dimension.card-type"],
        "classification": "confidential", "warnings": ["Use card-transaction grain."],
    },
    {
        "id": "late-payment-rate", "name": "Late Payment Rate", "entity": "loan-payment",
        "description": "Percentage of loan-payment events flagged late.",
        "measure": {"kind": "ratio", "numerator": {"kind": "aggregate", "aggregation": "count", "source": None, "predicates": [{"source": {"table": "table.loan_payments", "column": "late_payment_flag"}, "operator": "eq", "value": 1}]}, "denominator": {"kind": "aggregate", "aggregation": "count", "source": None, "predicates": []}, "scale": 100.0},
        "dependencies": ["table.loan_payments"], "dimensions": ["dimension.loan-type"],
        "classification": "confidential", "warnings": ["Preserve payment-event denominator before joining loans."],
    },
    {
        "id": "non-performing-loan-rate", "name": "Non-performing Loan Rate", "entity": "loan",
        "description": "Percentage of loans with Defaulted or Written Off status.",
        "measure": {"kind": "ratio", "numerator": {"kind": "aggregate", "aggregation": "count", "source": None, "predicates": [{"source": {"table": "table.loans", "column": "status"}, "operator": "in", "value": ["Defaulted", "Written Off"]}]}, "denominator": {"kind": "aggregate", "aggregation": "count", "source": None, "predicates": []}, "scale": 100.0},
        "dependencies": ["table.loans"], "dimensions": ["dimension.loan-type", "dimension.loan-status", "dimension.branch"],
        "classification": "confidential", "warnings": ["Use originated-loan grain."],
    },
    {
        "id": "customer-net-cash-flow", "name": "Customer Net Cash Flow", "entity": "customer",
        "description": "Net signed account-transaction amount by customer and servicing branch.",
        "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "table.transactions", "column": "amount"}, "predicates": []},
        "dependencies": ["table.customers", "table.accounts", "table.transactions", "table.branches"],
        "dimensions": ["dimension.customer-gender", "dimension.customer-age", "dimension.branch", "dimension.transaction-type", "dimension.transaction-channel", "dimension.transaction-date"],
        "time_dimension": "dimension.transaction-date", "relative_time_anchor": "max_available_date",
        "formula": "SUM(CASE WHEN transactions.txn_type IN ('Deposit', 'Transfer In', 'Interest Credit') THEN transactions.amount ELSE -transactions.amount END) FROM customers JOIN accounts ON customers.customer_id = accounts.customer_id JOIN transactions ON accounts.account_id = transactions.account_id JOIN branches ON accounts.branch_id = branches.branch_id",
        "classification": "restricted", "warnings": ["Join customers to accounts before transactions; the branch join is many-to-one and does not change transaction grain."],
    },
    {
        "id": "branch-fraud-exposure", "name": "Branch Fraud Exposure", "entity": "branch",
        "description": "Total fraudulent card-transaction amount attributed to the account's servicing branch.",
        "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "table.card_transactions", "column": "amount"}, "predicates": [{"source": {"table": "table.card_transactions", "column": "is_fraud"}, "operator": "eq", "value": 1}]},
        "dependencies": ["table.branches", "table.accounts", "table.cards", "table.card_transactions"],
        "dimensions": ["dimension.branch", "dimension.card-type", "dimension.merchant-category"],
        "formula": "SUM(CASE WHEN card_transactions.is_fraud = 1 THEN card_transactions.amount ELSE 0 END) FROM branches JOIN accounts ON branches.branch_id = accounts.branch_id JOIN cards ON accounts.account_id = cards.account_id JOIN card_transactions ON cards.card_id = card_transactions.card_id",
        "classification": "confidential", "warnings": ["Preserve card-transaction grain across the three many-to-one joins."],
    },
    {
        "id": "branch-toi-proxy", "name": "Branch TOI Proxy", "entity": "branch",
        "description": "Partial branch operating-income proxy calculated as fee debits less interest credits; it is not audited Total Operating Income.",
        "aliases": ["TOI", "TOI by branch", "total operating income by branch", "branch operating income proxy"],
        "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "table.transactions", "column": "amount"}, "predicates": []},
        "dependencies": ["table.branches", "table.accounts", "table.transactions"],
        "dimensions": ["dimension.branch", "dimension.transaction-type", "dimension.transaction-date"],
        "time_dimension": "dimension.transaction-date", "relative_time_anchor": "max_available_date",
        "formula": "SUM(CASE WHEN transactions.txn_type = 'Fee Debit' THEN transactions.amount WHEN transactions.txn_type = 'Interest Credit' THEN -transactions.amount ELSE 0 END) FROM branches JOIN accounts ON branches.branch_id = accounts.branch_id JOIN transactions ON accounts.account_id = transactions.account_id",
        "classification": "confidential",
        "warnings": [
            "Proxy only: the database lacks complete bank P&L components required for audited Total Operating Income.",
            "Exclude Deposit, Withdrawal, Transfer In, and Transfer Out because they are principal or customer cash movements, not operating revenue or expense.",
            "Preserve transaction grain when joining transactions to accounts and branches; group branches by branch_id and label them with branch_name.",
        ],
    },
    {
        "id": "customer-loan-repayment-total", "name": "Customer Loan Repayment Total", "entity": "customer",
        "description": "Total loan-payment amount by customer, loan attributes, and originating branch.",
        "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "table.loan_payments", "column": "amount_paid"}, "predicates": []},
        "dependencies": ["table.customers", "table.loans", "table.loan_payments", "table.branches"],
        "dimensions": ["dimension.customer-gender", "dimension.customer-age", "dimension.branch", "dimension.loan-type", "dimension.loan-status"],
        "formula": "SUM(loan_payments.amount_paid) FROM customers JOIN loans ON customers.customer_id = loans.customer_id JOIN loan_payments ON loans.loan_id = loan_payments.loan_id JOIN branches ON loans.branch_id = branches.branch_id",
        "classification": "restricted", "warnings": ["Aggregate from loan-payment grain; customer and branch are many-to-one lookup joins."],
    },
    {
        "id": "supported-delinquency-population", "name": "Supported Delinquency Population", "entity": "customer",
        "description": "Distinct customers who have both a support ticket and at least one late loan payment.",
        "result_type": "integer",
        "measure": {"kind": "aggregate", "aggregation": "count_distinct", "source": {"table": "table.customers", "column": "customer_id"}, "predicates": []},
        "dependencies": ["table.customers", "table.support_tickets", "table.loans", "table.loan_payments"],
        "dimensions": ["dimension.customer-gender", "dimension.customer-age", "dimension.branch", "dimension.loan-type", "dimension.loan-status"],
        "formula": "COUNT(DISTINCT customers.customer_id) FROM customers JOIN support_tickets ON customers.customer_id = support_tickets.customer_id JOIN loans ON customers.customer_id = loans.customer_id JOIN loan_payments ON loans.loan_id = loan_payments.loan_id WHERE loan_payments.late_payment_flag = 1",
        "classification": "restricted", "warnings": ["Count distinct customers after joining two one-to-many paths to prevent ticket-payment fanout."],
    },
]

RULES = [
    ("active-customer", "Active Customer", "customer", "classification", "boolean", ["entity.transaction", "entity.card-transaction"], "Aggregate account and card activity independently to customer grain, combine the aggregates, and never union raw event rows."),
    ("fraudulent-card-transaction", "Fraudulent Card Transaction", "card-transaction", "predicate", "boolean", ["table.card_transactions"], "A card transaction is fraudulent when card_transactions.is_fraud equals 1."),
    ("late-loan-payment", "Late Loan Payment", "loan-payment", "predicate", "boolean", ["table.loan_payments"], "A loan payment is late when loan_payments.late_payment_flag equals 1."),
    ("non-performing-loan", "Non-performing Loan", "loan", "predicate", "boolean", ["table.loans"], "A loan is non-performing when loans.status is Defaulted or Written Off."),
    ("transaction-direction", "Transaction Direction", "transaction", "classification", "direction", ["dimension.transaction-type"], "Deposit, Transfer In, and Interest Credit are inflows; Withdrawal, Transfer Out, and Fee Debit are outflows."),
    ("relative-time-anchor", "Relative Time Anchor", "transaction", "time_anchor", "date", ["dimension.transaction-date"], "Interpret relative account-transaction periods from MAX(transactions.txn_date), not wall-clock time."),
    ("high-value-multichannel-customer", "High-value Multichannel Customer", "customer", "classification", "boolean", ["table.customers", "table.accounts", "table.transactions", "table.cards"], "A customer qualifies when their accounts have at least one active card and at least 100000 in signed transaction activity across two or more channels during the latest 90-day period; join customers to accounts, then aggregate the transaction and card branches independently at customer grain."),
    ("branch-fraud-escalation", "Branch Fraud Escalation", "branch", "classification", "boolean", ["table.branches", "table.accounts", "table.cards", "table.card_transactions"], "Escalate a branch when it has at least five fraudulent card transactions or at least 10000 in fraudulent card-transaction amount during the latest 30-day period; join branches to accounts, accounts to cards, and cards to card transactions while preserving card-transaction grain."),
    ("branch-toi-proxy-definition", "Branch TOI Proxy Definition", "branch", "aggregation_constraint", "boolean", ["metric.branch-toi-proxy", "dimension.transaction-type", "table.branches", "table.accounts", "table.transactions"], "A branch TOI proxy is compliant only when it equals the sum of Fee Debit amounts minus the sum of Interest Credit amounts at transaction grain, using transactions to accounts to branches; Deposit, Withdrawal, Transfer In, and Transfer Out must contribute zero, and the result must be labeled as a proxy because complete bank P&L components are unavailable."),
    ("delinquent-customer-support-priority", "Delinquent Customer Support Priority", "customer", "classification", "boolean", ["table.support_tickets", "table.customers", "table.loans", "table.loan_payments"], "Prioritize a customer when an open support ticket exists and any loan payment is flagged late during the latest 90-day period; join support tickets to customers, customers to loans, and loans to loan payments, then evaluate existence at customer grain to prevent fanout."),
    ("employee-risk-portfolio-assignment", "Employee Risk Portfolio Assignment", "employee", "classification", "boolean", ["table.employees", "table.branches", "table.loans", "table.customers"], "Flag an employee assignment for review when the employee's branch owns at least three defaulted or written-off loans held by customers with credit scores below 600; join employees to branches, branches to loans, and loans to customers."),
]


def write_doc(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OKFDocument(frontmatter=frontmatter, body=body.strip() + "\n").serialize(), encoding="utf-8")


def base_document(kind: str, object_id: str, title: str, description: str, links: list[str], cerebro: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": {"physical_table": "Table", "business_rule": "Business Rule"}.get(kind, kind.replace("_", " ").title()),
        "id": object_id, "title": title, "description": description, "status": "stable",
        "links": sorted(set(links)),
        "sources": [{"id": "semantic-definition", "resource": "docs/semantic-layer-definition.md", "title": "Cerebro semantic-layer definition"}],
        "provenance": {"origin": "human_reviewed", "source": "docs/semantic-layer-definition.md"},
        "cerebro": {"kind": kind, **cerebro},
    }


def entity_id(table_name: str) -> str:
    return SPECIAL_ENTITY_IDS.get(table_name, f"entity.{table_name.replace('_', '-').removesuffix('s')}")


def main() -> None:
    snapshot = DuckDBSource(DEFAULT_CONFIG).scan()
    root = DEFAULT_BUNDLE
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "bundle.yaml").write_text(yaml.safe_dump({
        "name": "bank-workshop", "version": "0.2.0", "generation_mode": "fallback",
        "review_state": "approved", "okf_version": "0.2", "semantic_profile_version": "0.1",
        "source": "bank workshop DuckDB catalog plus declared manifest",
        "google_okf_repository": "https://github.com/GoogleCloudPlatform/open-knowledge-format.git",
        "google_okf_commit": "ad30107c31c06aec8a7d5636e0d1058118604e6f",
    }, sort_keys=False), encoding="utf-8")
    write_doc(root / "index.md", {"okf_version": "0.2"}, "# Banking Semantic Layer\n\nGoverned banking Semantic Profile v0.1.")

    table_ids = [f"table.{table.name}" for table in snapshot.tables]
    write_doc(root / "datasets" / "bank-workshop.md", base_document(
        "dataset", "dataset.bank-workshop", "Bank Workshop Dataset",
        "Synthetic retail-banking reference dataset with ten related tables.", table_ids,
        {"classification": "restricted", "table_count": 10, "column_count": 75, "row_sampling": "disabled"},
    ), "# Bank Workshop Dataset\n\nCatalog structure is discovered; semantic definitions are reviewed.")

    relationship_links: dict[str, list[str]] = {table_id: [] for table_id in table_ids}
    for relationship in snapshot.relationships:
        relationship_links[f"table.{relationship.source_table}"].append(f"relationship.{relationship.id}")
        relationship_links[f"table.{relationship.target_table}"].append(f"relationship.{relationship.id}")

    for table in snapshot.tables:
        table_id = f"table.{table.name}"
        columns = [{"name": column.name, "data_type": column.data_type, "nullable": column.nullable, "classification": column.classification, "provenance": "discovered"} for column in table.columns]
        classification = "restricted" if any(item["classification"] == "restricted" for item in columns) else "confidential" if any(item["classification"] == "confidential" for item in columns) else "internal"
        document = base_document("physical_table", table_id, table.name.replace("_", " ").title(), table.description, ["dataset.bank-workshop", *relationship_links[table_id]], {
            "classification": classification, "physical": {"schema": table.schema_name, "table": table.name},
            "schema": table.schema_name, "grain": table.grain, "primary_key": table.primary_key, "columns": columns, "warnings": [],
        })
        document["resource"] = f"duckdb://bank/{table.schema_name}/{table.name}"
        document["sources"] = [{"id": "duckdb-catalog", "resource": "DuckDB information_schema", "title": "DuckDB catalog metadata"}]
        document["provenance"] = {"origin": "discovered", "source": "DuckDB information_schema"}
        write_doc(root / "tables" / f"{table.name}.md", document, f"# {document['title']}\n\n{table.description}\n\nGrain: **{table.grain}**.")

    table_by_name = {table.name: table for table in snapshot.tables}
    for table_name, title in ENTITY_NAMES.items():
        table = table_by_name[table_name]
        object_id = entity_id(table_name)
        write_doc(root / "entities" / f"{object_id.split('.', 1)[1]}.md", base_document("entity", object_id, title, f"Business entity for {title.lower()} records.", [f"table.{table_name}"], {
            "classification": "restricted" if table_name == "customers" else "internal",
            "physical_mapping": {"table": f"table.{table_name}", "key": [table.primary_key]},
            "grain": {"type": "event" if table_name in {"transactions", "card_transactions", "loan_payments"} else "entity", "description": table.grain, "key": [table.primary_key]}, "warnings": [],
        }), f"# {title}\n\n{title} is represented by one `{table_name}` record at its declared grain.")

    for dim_id, title, entity, bindings, semantic_type, metrics, derivation in DIMENSIONS:
        physical = [{"table": f"table.{table}", "column": column} for table, column in bindings]
        links = [f"entity.{entity}", *[item["table"] for item in physical], *[f"metric.{metric}" for metric in metrics]]
        write_doc(root / "dimensions" / f"{dim_id}.md", base_document("dimension", f"dimension.{dim_id}", title, f"Governed {title.lower()} dimension.", links, {
            "classification": "restricted" if dim_id in {"customer-gender", "customer-age"} else "internal",
            "entity": f"entity.{entity}", "physical_mappings": physical, "semantic_type": semantic_type,
            "derivation": derivation, "compatible_metrics": [f"metric.{metric}" for metric in metrics], "warnings": [],
        }), f"# {title}\n\n{derivation or f'Groups results by {title.lower()}.'}")

    for metric in METRICS:
        object_id = f"metric.{metric['id']}"
        dimensions = metric["dimensions"]
        links = [f"entity.{metric['entity']}", *metric["dependencies"], *dimensions]
        if metric.get("time_dimension"):
            links.append(metric["time_dimension"])
        formula = metric.get("formula") or metric_formula(metric["measure"])
        document = base_document("metric", object_id, metric["name"], metric["description"], links, {
            "classification": metric["classification"], "entity": f"entity.{metric['entity']}", "measure": metric["measure"],
            "dependencies": metric["dependencies"], "formula": formula,
            "metric_result_type": metric.get("result_type", "decimal"), "filters": [],
            "grain": {"type": "aggregate", "description": "Requested compatible dimensions"},
            "compatible_dimensions": dimensions, "time_dimension": metric.get("time_dimension"),
            "relative_time_anchor": metric.get("relative_time_anchor"), "warnings": metric["warnings"],
        })
        if metric.get("aliases"):
            document["aliases"] = metric["aliases"]
        write_doc(root / "metrics" / f"{metric['id']}.md", document, f"# {metric['name']}\n\n{metric['description']}\n\nFormula: `{formula}`")

    for rule_id, title, entity, rule_kind, output_type, dependencies, logic in RULES:
        write_doc(root / "rules" / f"{rule_id}.md", base_document("business_rule", f"rule.{rule_id}", title, logic, [f"entity.{entity}", *dependencies], {
            "classification": "restricted" if entity == "customer" else "confidential", "entity": f"entity.{entity}",
            "rule_kind": rule_kind, "output_type": output_type, "dependencies": dependencies, "logic": logic,
            "grain": {"type": "entity", "description": f"One {entity.replace('-', ' ')}"}, "warnings": [],
        }), f"# {title}\n\n{logic}")

    for relationship in snapshot.relationships:
        source_table, target_table = f"table.{relationship.source_table}", f"table.{relationship.target_table}"
        source_entity, target_entity = entity_id(relationship.source_table), entity_id(relationship.target_table)
        write_doc(root / "relationships" / f"{relationship.id}.md", base_document("relationship", f"relationship.{relationship.id}", relationship.id.replace("_", " ").title(), f"Declared join from {relationship.source_table}.{relationship.source_column} to {relationship.target_table}.{relationship.target_column}.", [source_table, target_table, source_entity, target_entity], {
            "classification": "internal", "edge_type": "physical_fk", "semantic": {"from": source_entity, "to": target_entity},
            "physical": {"source": {"table": source_table, "column": relationship.source_column}, "target": {"table": target_table, "column": relationship.target_column}},
            "source_table": source_table, "source_column": relationship.source_column, "target_table": target_table, "target_column": relationship.target_column,
            "cardinality": relationship.cardinality, "join_type": {"default": "left"},
            "validation": {"target_unique": "not_checked", "source_fk_coverage": "not_checked", "fanout": "not_checked"},
            "warnings": ["Declared relationship; source-row profiling is disabled."],
        }), f"# {relationship.id.replace('_', ' ').title()}\n\nUse the declared `{relationship.cardinality}` join.")

    write_doc(root / "policies" / "sensitive-banking-data.md", base_document("policy", "policy.sensitive-banking-data", "Sensitive Banking Data", "Treat synthetic identity, contact, financial, and credit fields as sensitive.", table_ids, {
        "classification": "restricted", "applies_to": table_ids, "rule": "Return aggregate results and minimize restricted fields.",
    }), "# Sensitive Banking Data\n\nSynthetic data receives the same handling as real restricted banking data.")

    for directory in ("datasets", "tables", "entities", "dimensions", "metrics", "rules", "relationships", "policies"):
        (root / directory / "index.md").write_text(f"# {directory.title()}\n", encoding="utf-8")
    object_count = sum(1 for path in root.rglob("*.md") if path.name not in {"index.md", "log.md"})
    print(f"Wrote {root} with {object_count} semantic objects")


if __name__ == "__main__":
    main()
