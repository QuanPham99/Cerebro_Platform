import pytest

from cerebro.bundle import BundleLoader, BundleValidator
from cerebro.paths import DEFAULT_BUNDLE


@pytest.fixture()
def bundle():
    return BundleLoader().load(DEFAULT_BUNDLE)


def test_golden_bundle_contract(bundle):
    report = BundleValidator().validate(bundle)
    assert report.valid, report.issues
    counts = {
        kind: sum(obj.profile_kind == kind for obj in bundle.objects)
        for kind in {obj.profile_kind for obj in bundle.objects}
    }
    assert counts == {
        "dataset": 1,
        "physical_table": 10,
        "entity": 10,
        "dimension": 11,
        "metric": 10,
        "business_rule": 10,
        "relationship": 11,
        "policy": 1,
    }
    tables = [obj for obj in bundle.objects if obj.profile_kind == "physical_table"]
    assert sum(len(obj.cerebro["columns"]) for obj in tables) == 75


@pytest.mark.parametrize(
    ("object_id", "mutation", "code"),
    [
        ("entity.customer", lambda obj: obj.links.append("table.missing"), "dangling_link"),
        ("relationship.account_customer", lambda obj: obj.cerebro.update(source_table="table.missing"), "missing_endpoint"),
        ("relationship.account_customer", lambda obj: obj.cerebro.update(cardinality="sometimes"), "invalid_cardinality"),
        ("relationship.account_customer", lambda obj: obj.cerebro.update(source_column="missing"), "undeclared_join_column"),
        ("relationship.account_customer", lambda obj: obj.cerebro["semantic"].update(**{"from": "entity.missing"}), "invalid_relationship_entity"),
        ("dimension.customer-gender", lambda obj: obj.cerebro.update(entity="entity.missing"), "invalid_dimension_entity"),
        ("dimension.customer-gender", lambda obj: obj.cerebro["physical_mappings"][0].update(column="missing"), "undeclared_dimension_column"),
        ("dimension.customer-gender", lambda obj: obj.cerebro.update(compatible_metrics=["entity.customer"]), "invalid_dimension_metric"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(dependencies=["table.missing"]), "invalid_metric_dependency_target"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(entity="table.card_transactions"), "invalid_metric_entity"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(compatible_dimensions=["entity.card"]), "invalid_metric_dimension"),
        ("metric.transaction-volume", lambda obj: obj.cerebro.update(time_dimension="entity.transaction"), "invalid_metric_time_dimension"),
        ("metric.transaction-volume", lambda obj: obj.cerebro["measure"]["source"].update(column="missing"), "undeclared_metric_column"),
        ("metric.transaction-volume", lambda obj: obj.cerebro["measure"].update(aggregation="median"), "invalid_metric_contract"),
        ("metric.card-fraud-rate", lambda obj: obj.cerebro.update(grain="aggregate"), "missing_metric_grain"),
        ("rule.active-customer", lambda obj: obj.cerebro.update(logic=""), "missing_rule_logic"),
        ("rule.active-customer", lambda obj: obj.cerebro.update(dependencies=["policy.sensitive-banking-data"]), "invalid_rule_dependency"),
        ("policy.sensitive-banking-data", lambda obj: obj.cerebro.update(applies_to=[]), "missing_policy_target"),
        ("policy.sensitive-banking-data", lambda obj: obj.cerebro.update(applies_to=["entity.customer"]), "invalid_policy_target"),
        ("entity.customer", lambda obj: obj.links.clear(), "semantic_links_mismatch"),
        ("entity.customer", lambda obj: setattr(obj, "type", "Metric"), "profile_kind_mismatch"),
        ("entity.customer", lambda obj: setattr(obj, "id", "metric.customer"), "invalid_profile_id"),
        ("entity.customer", lambda obj: obj.cerebro.update(classification="secretish"), "invalid_classification"),
        ("entity.customer", lambda obj: obj.provenance.update(origin="model-ish"), "invalid_provenance"),
    ],
)
def test_invalid_semantic_contracts_are_rejected(bundle, object_id, mutation, code):
    invalid = bundle.model_copy(deep=True)
    mutation(invalid.by_id()[object_id])
    report = BundleValidator().validate(invalid)
    assert not report.valid
    assert code in {issue.code for issue in report.issues}
