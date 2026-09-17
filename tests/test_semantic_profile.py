from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from cerebro.bundle import BundleLoader, BundleValidator, load_validated_bundle
from cerebro.models import (
    AggregateMeasure,
    BusinessRuleCandidate,
    DimensionCandidate,
    EntityCandidate,
    PhysicalColumnBinding,
    StructuredMetricCandidate,
)
from cerebro.paths import DEFAULT_BUNDLE, ROOT
from cerebro.retrieval import SemanticRetriever
from cerebro.evaluation import compare_semantic_oracle, compare_structural_oracle
from cerebro.semantic.compiler import canonical_object_id, normalize_reference


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def test_semantic_ids_use_one_canonical_lower_kebab_form():
    assert canonical_object_id("entity", "entity_customer") == "entity.entity-customer"
    assert canonical_object_id("entity", "Entity.entity_customer") == "entity.entity-customer"
    assert normalize_reference("entity", "entity_customer") == "entity.entity-customer"
    assert normalize_reference("entity", "entity.entity_customer") == "entity.entity-customer"
    assert normalize_reference("metric", "Balance By Region") == "metric.balance-by-region"
    assert normalize_reference("physical_table", "card_transactions") == "table.card_transactions"


def test_okf_v02_indexes_unknown_types_and_legacy_fields(tmp_path: Path):
    _write(tmp_path / "index.md", '---\nokf_version: "0.2"\n---\n\n# Test bundle')
    _write(tmp_path / "objects" / "index.md", "# Objects")
    _write(
        tmp_path / "objects" / "unknown.md",
        """
---
type: Domain Note
id: note.example
title: Example note
status: stable
custom_field: preserved
---

# Example
""",
    )
    _write(
        tmp_path / "objects" / "legacy.md",
        """
---
type: concept
id: concept.legacy
name: Legacy concept
status: active
provenance: {origin: human_reviewed, source: legacy.md}
---

# Legacy
""",
    )
    (tmp_path / "bundle.yaml").write_text(
        yaml.safe_dump({"name": "compat", "version": "1", "semantic_profile_version": "0.1"}),
        encoding="utf-8",
    )

    bundle = BundleLoader().load(tmp_path)
    assert bundle.okf_version == "0.2"
    assert bundle.semantic_profile_version == "0.1"
    assert len(bundle.objects) == 2
    unknown = bundle.by_id()["note.example"]
    assert unknown.profile_kind == "generic"
    assert unknown.name == unknown.title == "Example note"
    assert unknown.model_extra == {"custom_field": "preserved"}
    legacy = bundle.by_id()["concept.legacy"]
    assert legacy.profile_kind == "legacy_concept"
    assert legacy.status == "stable"


def test_profile_candidate_models_are_strict_and_typed():
    entity = EntityCandidate(
        id="customer",
        name="Customer",
        description="A banking customer.",
        aliases=["client"],
        classification="restricted",
        physical_mapping={"table": "customers", "key": ["customer_id"]},
        grain={"type": "entity", "description": "One customer", "key": ["customer_id"]},
        warnings=[],
    )
    dimension = DimensionCandidate(
        id="customer-gender",
        name="Customer gender",
        description="Customer gender category.",
        entity="customer",
        physical_mappings=[{"table": "customers", "column": "gender"}],
        semantic_type="categorical",
        compatible_metrics=["customer-count"],
        classification="internal",
        warnings=[],
    )
    metric = StructuredMetricCandidate(
        id="customer-count",
        name="Customer count",
        description="Distinct banking customers.",
        entity="customer",
        measure=AggregateMeasure(
            aggregation="count_distinct",
            source=PhysicalColumnBinding(table="customers", column="customer_id"),
        ),
        dependencies=["customers"],
        grain={"type": "aggregate", "description": "Requested dimensions"},
        compatible_dimensions=["customer-gender"],
        classification="restricted",
        warnings=[],
    )
    rule = BusinessRuleCandidate(
        id="active-customer",
        name="Active customer",
        description="Customer with qualifying activity.",
        entity="customer",
        rule_kind="classification",
        output_type="boolean",
        dependencies=["transaction"],
        logic="Aggregate transaction activity to customer grain before classification.",
        grain={"type": "entity", "description": "One customer"},
        classification="restricted",
        warnings=[],
    )

    assert entity.physical_mapping.key == ["customer_id"]
    assert dimension.physical_mappings[0].column == "gender"
    assert metric.measure.kind == "aggregate"
    assert rule.rule_kind == "classification"


def test_golden_semantic_profile_contract_and_grounding():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    counts: dict[str, int] = {}
    for obj in bundle.objects:
        counts[obj.profile_kind] = counts.get(obj.profile_kind, 0) + 1
    assert counts == {
        "domain": 4,
        "dataset": 1,
        "physical_table": 10,
        "entity": 10,
        "dimension": 11,
        "metric": 12,
        "business_rule": 12,
        "relationship": 11,
        "policy": 3,
    }
    assert len(bundle.objects) == 74
    assert sum(
        len(obj.cerebro.get("columns", []))
        for obj in bundle.objects
        if obj.profile_kind == "physical_table"
    ) == 75
    report = BundleValidator().validate(bundle)
    assert report.valid, report.issues

    grounding = SemanticRetriever(bundle).grounding("fraud rate by merchant category")
    assert "metric.card-fraud-rate" in {item["id"] for item in grounding.metrics}
    assert "dimension.merchant-category" in {item["id"] for item in grounding.dimensions}
    assert "entity.card-transaction" in {item["id"] for item in grounding.entities}
    assert "table.card_transactions" in {item["id"] for item in grounding.tables}


def test_branch_toi_proxy_is_grounded_governed_and_calculable():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    by_id = bundle.by_id()
    metric = by_id["metric.branch-toi-proxy"]
    rule = by_id["rule.branch-toi-proxy-definition"]

    assert metric.cerebro["metric_result_type"] == "decimal"
    assert metric.cerebro["compatible_dimensions"] == [
        "dimension.branch",
        "dimension.transaction-type",
        "dimension.transaction-date",
    ]
    assert set(metric.cerebro["dependencies"]) == {
        "table.branches",
        "table.accounts",
        "table.transactions",
    }
    assert "Fee Debit" in metric.cerebro["formula"]
    assert "Interest Credit" in metric.cerebro["formula"]
    assert "metric.branch-toi-proxy" in rule.cerebro["dependencies"]
    assert "proxy" in rule.cerebro["logic"].lower()

    graph = SemanticRetriever(bundle).graph()
    metric_edges = {
        edge.target
        for edge in graph.edges
        if edge.source == metric.id and edge.type == "metric_dependency"
    }
    rule_edges = {
        edge.target
        for edge in graph.edges
        if edge.source == rule.id and edge.type == "rule_dependency"
    }
    assert set(metric.cerebro["dependencies"]) <= metric_edges
    assert "metric.branch-toi-proxy" in rule_edges

    grounding = SemanticRetriever(bundle).grounding("TOI by branch")
    assert metric.id in {item["id"] for item in grounding.metrics}
    assert rule.id in {item["id"] for item in grounding.rules}

    with duckdb.connect(str(ROOT / "data" / "workshop.duckdb"), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT branches.branch_id,
                   SUM(CASE
                       WHEN transactions.txn_type = 'Fee Debit' THEN transactions.amount
                       WHEN transactions.txn_type = 'Interest Credit' THEN -transactions.amount
                       ELSE 0
                   END) AS branch_toi_proxy
            FROM branches
            JOIN accounts ON branches.branch_id = accounts.branch_id
            JOIN transactions ON accounts.account_id = transactions.account_id
            GROUP BY branches.branch_id
            """
        ).fetchall()

    values = [value for _, value in rows]
    assert len(rows) == 150
    assert all(value is not None for value in values)
    assert min(values) < 0 < max(values)


def test_golden_showcase_definitions_each_span_at_least_three_joins():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    graph = SemanticRetriever(bundle).graph()
    showcase_ids = {
        "metric.customer-net-cash-flow",
        "metric.branch-fraud-exposure",
        "metric.customer-loan-repayment-total",
        "metric.supported-delinquency-population",
        "rule.high-value-multichannel-customer",
        "rule.branch-fraud-escalation",
        "rule.delinquent-customer-support-priority",
        "rule.employee-risk-portfolio-assignment",
    }
    relationships = []
    for obj in bundle.objects:
        if obj.profile_kind != "relationship":
            continue
        physical = obj.cerebro["physical"]
        relationships.append({physical["source"]["table"], physical["target"]["table"]})

    assert showcase_ids <= bundle.by_id().keys()
    for object_id in showcase_ids:
        dependencies = set(bundle.by_id()[object_id].cerebro["dependencies"])
        assert len(dependencies) >= 4
        edge_type = "metric_dependency" if object_id.startswith("metric.") else "rule_dependency"
        rendered_dependencies = {
            edge.target
            for edge in graph.edges
            if edge.source == object_id and edge.type == edge_type
        }
        assert dependencies <= rendered_dependencies
        join_edges = [edge for edge in relationships if edge <= dependencies]
        assert len(join_edges) >= 3

        reachable = {next(iter(dependencies))}
        while True:
            expanded = reachable | set().union(
                *(edge for edge in join_edges if edge & reachable),
                set(),
            )
            if expanded == reachable:
                break
            reachable = expanded
        assert reachable == dependencies


def test_profile_validator_rejects_invalid_dimension_binding():
    bundle = load_validated_bundle(DEFAULT_BUNDLE).model_copy(deep=True)
    dimension = bundle.by_id()["dimension.transaction-channel"]
    dimension.cerebro["physical_mappings"][0]["column"] = "invented"
    report = BundleValidator().validate(bundle)
    assert "undeclared_dimension_column" in {issue.code for issue in report.issues}


def test_progressive_grounding_is_bounded_and_semantic_oracle_is_kind_aware():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    grounding = SemanticRetriever(bundle).grounding("fraud rate by card type")
    selected_count = sum(len(items) for items in (
        grounding.entities,
        grounding.dimensions,
        grounding.metrics,
        grounding.rules,
        grounding.tables,
        grounding.joins,
        grounding.concepts,
    ))
    assert selected_count <= 10
    assert {item["id"] for item in grounding.metrics} == {"metric.card-fraud-rate"}
    assert {item["id"] for item in grounding.dimensions} == {"dimension.card-type"}

    report = compare_semantic_oracle(DEFAULT_BUNDLE)
    assert report["precision"] == report["recall"] == 1.0
    assert report["matched"] == 56
    assert all(item["recall"] == 1.0 for item in report["by_kind"].values())

    structural = compare_structural_oracle(DEFAULT_BUNDLE)
    assert structural["precision"] == structural["recall"] == 1.0
    assert set(structural["by_kind"]) == {
        "dimension", "entity", "physical_table", "policy", "relationship",
    }
    assert structural["deferred_kinds"] == ["business_rule", "metric"]
