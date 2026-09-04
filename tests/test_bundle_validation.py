import pytest

from cerebro.bundle import BundleLoader, BundleValidator
from cerebro.paths import DEFAULT_BUNDLE


@pytest.fixture()
def bundle():
    return BundleLoader().load(DEFAULT_BUNDLE)


def test_golden_bundle_contract(bundle):
    report = BundleValidator().validate(bundle)
    assert report.valid, report.issues
    counts = {kind: sum(obj.type == kind for obj in bundle.objects) for kind in {obj.type for obj in bundle.objects}}
    assert counts == {"dataset": 1, "table": 10, "concept": 9, "relationship": 11, "metric": 4, "policy": 1}
    tables = [obj for obj in bundle.objects if obj.type == "table"]
    assert sum(len(obj.cerebro["columns"]) for obj in tables) == 75


@pytest.mark.parametrize(
    ("object_id", "mutation", "code"),
    [
        ("concept.card-fraud", lambda obj: obj.links.append("table.missing"), "dangling_link"),
        ("relationship.account_customer", lambda obj: obj.cerebro.update(source_table="table.missing"), "missing_endpoint"),
        ("relationship.account_customer", lambda obj: obj.cerebro.update(cardinality="sometimes"), "invalid_cardinality"),
        ("relationship.account_customer", lambda obj: obj.cerebro.update(source_column="missing"), "undeclared_join_column"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(dependencies=["table.missing"]), "unresolved_metric_dependency"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(filters="is_fraud = 1"), "invalid_metric_filter"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(formula=""), "invalid_metric_formula"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(formula="SUM(invented.amount)"), "undeclared_formula_reference"),
        ("concept.card-fraud", lambda obj: obj.cerebro.update(maps_to=["metric.card-fraud-rate"]), "missing_concept_table_mapping"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(dependencies=[]), "missing_metric_dependency"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(dependencies=["concept.card-fraud"]), "invalid_metric_dependency_target"),
        ("policy.sensitive-banking-data", lambda obj: obj.cerebro.update(applies_to=[]), "missing_policy_target"),
        ("policy.sensitive-banking-data", lambda obj: obj.cerebro.update(applies_to=["concept.card-fraud"]), "invalid_policy_target"),
        ("concept.card-fraud", lambda obj: obj.links.pop(), "semantic_links_mismatch"),
        ("concept.card-fraud", lambda obj: obj.cerebro.update(classification="secretish"), "invalid_classification"),
        ("concept.card-fraud", lambda obj: obj.provenance.update(origin="model-ish"), "invalid_provenance"),
    ],
)
def test_invalid_semantic_contracts_are_rejected(bundle, object_id, mutation, code):
    invalid = bundle.model_copy(deep=True)
    mutation(invalid.by_id()[object_id])
    report = BundleValidator().validate(invalid)
    assert not report.valid
    assert code in {issue.code for issue in report.issues}
