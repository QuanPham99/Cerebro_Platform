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
        "domain": 4,
        "dataset": 1,
        "physical_table": 10,
        "entity": 10,
        "dimension": 11,
        "metric": 11,
        "business_rule": 11,
        "relationship": 11,
        "policy": 3,
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
        ("entity.customer", lambda obj: obj.cerebro.update(domain="domain.missing"), "invalid_entity_domain"),
        ("domain.retail-banking", lambda obj: setattr(obj, "description", ""), "invalid_domain_contract"),
        ("domain.retail-banking", lambda obj: setattr(obj, "id", "entity.retail-banking"), "invalid_profile_id"),
    ],
)
def test_invalid_semantic_contracts_are_rejected(bundle, object_id, mutation, code):
    invalid = bundle.model_copy(deep=True)
    mutation(invalid.by_id()[object_id])
    report = BundleValidator().validate(invalid)
    assert not report.valid
    assert code in {issue.code for issue in report.issues}


def test_entity_without_a_declared_domain_still_validates(bundle):
    """Domain is optional (spec 026): an entity not yet tagged must not fail validation."""
    valid = bundle.model_copy(deep=True)
    entity = valid.by_id()["entity.customer"]
    entity.cerebro.pop("domain", None)
    entity.links = [link for link in entity.links if not link.startswith("domain.")]
    report = BundleValidator().validate(valid)
    assert report.valid, report.issues
