"""Build the checked-in bank workshop OKF bundle from catalog-only metadata."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from cerebro.paths import DEFAULT_BUNDLE, DEFAULT_CONFIG
from cerebro.source import DuckDBSource
from cerebro.upstream import OKFDocument


CONCEPTS = [
    {
        "id": "concept.customer-demographics",
        "name": "Customer demographics",
        "description": "Customer count and composition by gender, geography, occupation, and age attributes.",
        "aliases": ["customer count", "clients by gender", "customer profile"],
        "maps_to": ["table.customers"],
        "classification": "restricted",
        "warnings": ["Use restricted identity attributes only when necessary; prefer aggregate output."],
    },
    {
        "id": "concept.account-balance",
        "name": "Account balance",
        "description": "Current account balance analyzed by account type, status, customer, or branch.",
        "aliases": ["average balance", "avg account balance", "deposits by account type"],
        "maps_to": ["table.accounts"],
        "classification": "confidential",
        "warnings": ["Balance is a current snapshot, not a transaction flow."],
    },
    {
        "id": "concept.support-workload",
        "name": "Support workload",
        "description": "Open, resolved, and escalated customer support cases by issue type.",
        "aliases": ["open support tickets", "customer issues", "support cases"],
        "maps_to": ["table.support_tickets", "table.customers"],
        "classification": "internal",
        "warnings": ["Satisfaction score is meaningful only for resolved cases."],
    },
    {
        "id": "concept.card-fraud",
        "name": "Card fraud",
        "description": "Fraud incidence across card transactions, card types, customers, and accounts.",
        "aliases": ["fraud rate", "fraud percentage", "card fraud percent", "is_fraud"],
        "maps_to": ["table.card_transactions", "table.cards", "metric.card-fraud-rate"],
        "classification": "confidential",
        "warnings": ["Use card transaction grain; do not mix directly with account transactions."],
    },
    {
        "id": "concept.branch-performance",
        "name": "Branch performance",
        "description": "Transaction amount, lending outcomes, and staffing analyzed by branch.",
        "aliases": ["branch transaction amount", "branch lending", "branch comparison"],
        "maps_to": ["table.branches", "table.accounts", "table.transactions", "table.loans", "table.employees"],
        "classification": "internal",
        "warnings": ["Account transactions reach branches through accounts; use both declared joins."],
    },
    {
        "id": "concept.repayment-behavior",
        "name": "Loan repayment behavior",
        "description": "Late-payment incidence and payment composition by loan and loan type.",
        "aliases": ["late payment rate", "repayment delinquency", "loan installments"],
        "maps_to": ["table.loan_payments", "table.loans", "metric.late-payment-rate"],
        "classification": "confidential",
        "warnings": ["Payment events and loans have different grains; aggregate before comparing loan types."],
    },
    {
        "id": "concept.transaction-activity",
        "name": "Account transaction activity",
        "description": "Unsigned account ledger events whose direction is determined by transaction type.",
        "aliases": ["monthly transaction growth", "transaction volume", "money movement"],
        "maps_to": ["table.transactions", "table.accounts", "metric.transaction-volume"],
        "classification": "confidential",
        "warnings": ["Amounts are positive; derive inflow/outflow from txn_type.", "Anchor recent periods to MAX(transactions.txn_date)."],
    },
    {
        "id": "concept.active-customer",
        "name": "Active customer",
        "description": "Customer activity derived separately from account and card logs before customer-level combination.",
        "aliases": ["top active customers", "top 5 percent customers", "account and card activity"],
        "maps_to": ["table.customers", "table.accounts", "table.transactions", "table.cards", "table.card_transactions"],
        "classification": "restricted",
        "warnings": ["Aggregate each activity log to customer grain before combining; never use a naive UNION of raw events."],
    },
    {
        "id": "concept.bad-debt",
        "name": "Bad debt",
        "description": "Loans with Defaulted or Written Off status compared with total originated loans.",
        "aliases": ["bad debt rate", "non performing loan", "defaulted written off"],
        "maps_to": ["table.loans", "table.branches", "table.employees", "metric.non-performing-loan-rate"],
        "classification": "confidential",
        "warnings": ["Loan Officer headcount and loans must each aggregate to branch grain before comparison."],
    },
]

METRICS = [
    {
        "id": "metric.transaction-volume",
        "name": "Transaction volume",
        "description": "Total positive account transaction amount for a defined period and scope.",
        "aliases": ["transaction amount", "monthly volume", "total transactions"],
        "dependencies": ["table.transactions"],
        "formula": "SUM(transactions.amount)",
        "filters": [],
        "grain": "requested dimensions over account transaction events",
        "warnings": ["Direction requires txn_type; amount itself is unsigned.", "Use MAX(txn_date) as the relative-time anchor."],
    },
    {
        "id": "metric.card-fraud-rate",
        "name": "Card fraud rate",
        "description": "Percentage of card transactions flagged as fraud.",
        "aliases": ["fraud percent", "fraud rate by card type"],
        "dependencies": ["table.card_transactions"],
        "formula": "100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0)",
        "filters": [],
        "grain": "aggregate over card transaction events",
        "warnings": ["Safe division returns NULL for an empty population."],
    },
    {
        "id": "metric.late-payment-rate",
        "name": "Late payment rate",
        "description": "Percentage of loan payment events flagged late.",
        "aliases": ["late payments by loan type", "payment delinquency rate"],
        "dependencies": ["table.loan_payments"],
        "formula": "100.0 * SUM(loan_payments.late_payment_flag) / NULLIF(COUNT(*), 0)",
        "filters": [],
        "grain": "aggregate over loan payment events",
        "warnings": ["Join loans only after preserving payment-event denominator."],
    },
    {
        "id": "metric.non-performing-loan-rate",
        "name": "Non-performing loan rate",
        "description": "Percentage of loans that are Defaulted or Written Off.",
        "aliases": ["bad debt rate", "default rate", "NPL rate"],
        "dependencies": ["table.loans"],
        "formula": "100.0 * SUM(CASE WHEN loans.status IN ('Defaulted', 'Written Off') THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)",
        "filters": [],
        "grain": "aggregate over originated loans",
        "warnings": ["Safe division returns NULL for an empty population."],
    },
]


def write_doc(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = OKFDocument(frontmatter=frontmatter, body=body.strip() + "\n")
    path.write_text(document.serialize(), encoding="utf-8")


def main() -> None:
    snapshot = DuckDBSource(DEFAULT_CONFIG).scan()
    root = DEFAULT_BUNDLE
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "bank-workshop",
                "version": snapshot.source_version,
                "generation_mode": "fallback",
                "source": "bank workshop DuckDB catalog plus declared manifest",
                "google_okf_repository": "https://github.com/GoogleCloudPlatform/open-knowledge-format.git",
                "google_okf_commit": "ad30107c31c06aec8a7d5636e0d1058118604e6f",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    write_doc(
        root / "index.md",
        {"type": "index", "name": "Bank workshop semantic layer"},
        "# Bank workshop semantic layer\n\nA reviewed golden OKF bundle generated without source-row access.",
    )
    table_ids = [f"table.{table.name}" for table in snapshot.tables]
    write_doc(
        root / "datasets" / "bank-workshop.md",
        {
            "type": "dataset",
            "id": "dataset.bank-workshop",
            "name": "Bank workshop dataset",
            "description": "Synthetic retail banking reference dataset with ten related tables.",
            "status": "active",
            "tags": ["banking", "duckdb", "synthetic"],
            "links": table_ids + ["policy.sensitive-banking-data"],
            "provenance": {"origin": "human_reviewed", "source": "config/bank-source.yaml"},
            "cerebro": {"classification": "restricted", "table_count": 10, "column_count": 75, "row_sampling": "disabled"},
        },
        "# Bank workshop dataset\n\nCatalog structure is discovered; keys, relationships, and business rules are declared and reviewed.",
    )
    relationship_links: dict[str, list[str]] = {table_id: [] for table_id in table_ids}
    for relationship in snapshot.relationships:
        relationship_links[f"table.{relationship.source_table}"].append(f"relationship.{relationship.id}")
        relationship_links[f"table.{relationship.target_table}"].append(f"relationship.{relationship.id}")
    concept_links: dict[str, list[str]] = {table_id: [] for table_id in table_ids}
    for concept in CONCEPTS:
        for mapped in concept["maps_to"]:
            if mapped in concept_links:
                concept_links[mapped].append(concept["id"])
    for table in snapshot.tables:
        table_id = f"table.{table.name}"
        columns = [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "classification": column.classification,
                "provenance": "discovered",
            }
            for column in table.columns
        ]
        classification = "restricted" if any(col["classification"] == "restricted" for col in columns) else (
            "confidential" if any(col["classification"] == "confidential" for col in columns) else "internal"
        )
        warnings = []
        if table.name == "transactions":
            warnings = ["Amounts are positive; use txn_type for direction.", "Anchor relative time to MAX(txn_date).", "Do not UNION raw rows with card_transactions."]
        elif table.name == "card_transactions":
            warnings = ["Card-event grain differs from account transactions.", "Anchor relative time to MAX(txn_date)."]
        write_doc(
            root / "tables" / f"{table.name}.md",
            {
                "type": "table",
                "id": table_id,
                "name": table.name.replace("_", " ").title(),
                "description": table.description,
                "status": "active",
                "aliases": table.aliases,
                "tags": ["banking", table.name],
                "links": ["dataset.bank-workshop", *relationship_links[table_id], *concept_links[table_id], "policy.sensitive-banking-data"],
                "provenance": {"origin": "human_reviewed", "catalog": "DuckDB information_schema", "semantics": "config/bank-source.yaml"},
                "cerebro": {
                    "classification": classification,
                    "schema": table.schema_name,
                    "grain": table.grain,
                    "primary_key": table.primary_key,
                    "columns": columns,
                    "warnings": warnings,
                },
            },
            f"# {table.name.replace('_', ' ').title()}\n\n{table.description}\n\nGrain: **{table.grain}**.",
        )
    for relationship in snapshot.relationships:
        write_doc(
            root / "relationships" / f"{relationship.id}.md",
            {
                "type": "relationship",
                "id": f"relationship.{relationship.id}",
                "name": relationship.id.replace("_", " ").title(),
                "description": f"Declared join from {relationship.source_table}.{relationship.source_column} to {relationship.target_table}.{relationship.target_column}.",
                "status": "active",
                "tags": ["join", "physical-fk"],
                "links": [f"table.{relationship.source_table}", f"table.{relationship.target_table}"],
                "provenance": {"origin": "human_reviewed", "source": "config/bank-source.yaml", "database_constraint": False},
                "cerebro": {
                    "classification": "internal",
                    "edge_type": "physical_fk",
                    "source_table": f"table.{relationship.source_table}",
                    "source_column": relationship.source_column,
                    "target_table": f"table.{relationship.target_table}",
                    "target_column": relationship.target_column,
                    "cardinality": relationship.cardinality,
                    "warnings": ["Declared relationship; the source DuckDB does not define FK constraints."],
                },
            },
            f"# {relationship.id.replace('_', ' ').title()}\n\nUse an exact ID join with `{relationship.cardinality}` cardinality.",
        )
    for concept in CONCEPTS:
        payload = dict(concept)
        object_id = payload.pop("id")
        name = payload.pop("name")
        description = payload.pop("description")
        aliases = payload.pop("aliases")
        write_doc(
            root / "concepts" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "concept",
                "id": object_id,
                "name": name,
                "description": description,
                "status": "active",
                "aliases": aliases,
                "tags": ["business-concept", "banking"],
                "links": payload["maps_to"],
                "provenance": {"origin": "human_reviewed", "source": "docs/semantic-layer-definition.md"},
                "cerebro": payload,
            },
            f"# {name}\n\n{description}",
        )
    for metric in METRICS:
        payload = dict(metric)
        object_id = payload.pop("id")
        name = payload.pop("name")
        description = payload.pop("description")
        aliases = payload.pop("aliases")
        write_doc(
            root / "metrics" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "metric",
                "id": object_id,
                "name": name,
                "description": description,
                "status": "active",
                "aliases": aliases,
                "tags": ["metric", "banking"],
                "links": payload["dependencies"],
                "provenance": {"origin": "human_reviewed", "source": "docs/semantic-layer-definition.md"},
                "cerebro": {"classification": "confidential", **payload},
            },
            f"# {name}\n\n{description}\n\nFormula: `{payload['formula']}`",
        )
    write_doc(
        root / "policies" / "sensitive-banking-data.md",
        {
            "type": "policy",
            "id": "policy.sensitive-banking-data",
            "name": "Sensitive banking data",
            "description": "Treat synthetic identity, contact, financial, and credit fields as sensitive.",
            "status": "active",
            "aliases": ["PII policy", "restricted data"],
            "tags": ["policy", "classification"],
            "links": table_ids,
            "provenance": {"origin": "human_reviewed", "source": "config/bank-source.yaml"},
            "cerebro": {"classification": "restricted", "applies_to": table_ids, "rule": "Return aggregate results and minimize restricted fields."},
        },
        "# Sensitive banking data\n\nSynthetic data receives the same handling as real restricted banking data.",
    )
    for directory in ("datasets", "tables", "concepts", "relationships", "metrics", "policies"):
        write_doc(root / directory / "index.md", {"type": "index", "name": directory.title()}, f"# {directory.title()}")
    print(f"Wrote {root} with {sum(1 for _ in root.rglob('*.md'))} Markdown documents")


if __name__ == "__main__":
    main()

