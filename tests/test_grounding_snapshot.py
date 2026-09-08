from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from cerebro.bundle import load_validated_bundle
from cerebro.models import (
    AuthorizationScope,
    GroundingSnapshot,
    SemanticBundle,
    SemanticObject,
    SQLGenerationRequest,
)
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.provenance import (
    authorization_scope_sha256,
    canonical_json_bytes,
    canonical_question_sha256,
    canonicalize_question,
    grounding_snapshot_sha256,
)
from cerebro.retrieval import (
    AuthorizationScopeIntegrityError,
    GroundingResolver,
    SemanticRetriever,
    SnapshotMetadataError,
    UnsupportedDialectError,
)

TENANT_HASH = "a" * 64
RETRIEVAL_CONFIG_HASH = "c" * 64

BANK_CLASSIFICATIONS = frozenset({"public", "internal", "confidential"})


def _scope(
    allowed_object_ids,
    *,
    allowed_classifications=BANK_CLASSIFICATIONS,
    policy_version: str = "policy.v1",
    tenant_scope_hash: str = TENANT_HASH,
) -> AuthorizationScope:
    """Build a scope whose declared hash is genuinely derived from its payload."""
    draft = AuthorizationScope(
        scope_version="008.scope.v1",
        tenant_scope_hash=tenant_scope_hash,
        policy_version=policy_version,
        allowed_object_ids=frozenset(allowed_object_ids),
        allowed_classifications=frozenset(allowed_classifications),
        authorization_scope_hash="0" * 64,
    )
    return draft.model_copy(
        update={"authorization_scope_hash": authorization_scope_sha256(draft)}
    )


@pytest.fixture(scope="module")
def bundle() -> SemanticBundle:
    return load_validated_bundle(DEFAULT_BUNDLE)


@pytest.fixture(scope="module")
def retriever(bundle: SemanticBundle) -> SemanticRetriever:
    return SemanticRetriever(bundle)


@pytest.fixture()
def resolver(retriever: SemanticRetriever) -> GroundingResolver:
    return GroundingResolver(retriever, retrieval_config_hash=RETRIEVAL_CONFIG_HASH)


@pytest.fixture()
def account_scope() -> AuthorizationScope:
    return _scope({"table.accounts", "table.branches", "relationship.account_branch"})


def _synthetic_bundle(objects: list[SemanticObject]) -> SemanticBundle:
    return SemanticBundle(
        name="synthetic", version="9.9.9", root="/synthetic", objects=objects
    )


def _synthetic_table(
    object_id: str = "table.orders",
    *,
    classification: str = "internal",
    columns: list[dict] | None = None,
) -> SemanticObject:
    return SemanticObject(
        id=object_id,
        type="table",
        name=object_id.split(".", 1)[1],
        description="Synthetic governed table.",
        cerebro={
            "classification": classification,
            "grain": "one row per order",
            "columns": columns
            if columns is not None
            else [
                {
                    "name": "order_id",
                    "data_type": "BIGINT",
                    "classification": "internal",
                },
                {
                    "name": "amount",
                    "data_type": "DOUBLE",
                    "classification": "confidential",
                },
            ],
        },
    )


# --- canonical question and identity helpers --------------------------------


def test_canonicalize_question_is_versioned_unicode_nfc_and_whitespace_only():
    assert (
        canonicalize_question("  Revenu\u0065\u0301\u00a0 for\t Q1  ")
        == "Revenu\u00e9 for Q1"
    )
    assert canonicalize_question("Q1") != canonicalize_question("q1")
    assert canonicalize_question("a\n\n\tb") == "a b"
    assert canonicalize_question("   ") == ""


def test_canonical_question_sha256_hashes_exact_utf8_bytes():
    canonical = canonicalize_question(" caf\u00e9  volume ")
    assert canonical == "caf\u00e9 volume"
    assert (
        canonical_question_sha256(canonical)
        == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    with pytest.raises(ValueError):
        canonical_question_sha256("  not canonical  ")


def test_scope_hash_excludes_its_own_field_and_is_reproducible(account_scope):
    assert (
        authorization_scope_sha256(account_scope)
        == account_scope.authorization_scope_hash
    )
    reloaded = AuthorizationScope.model_validate_json(account_scope.model_dump_json())
    assert (
        authorization_scope_sha256(reloaded) == account_scope.authorization_scope_hash
    )
    assert b"authorization_scope_hash" not in canonical_json_bytes(
        account_scope, exclude={"authorization_scope_hash"}
    )


# --- authorization happens before retrieval ---------------------------------


def test_unauthorized_high_score_object_never_enters_snapshot(resolver):
    scope = _scope({"table.accounts"})
    snapshot = resolver.resolve(
        canonicalize_question("show confidential customer identity details"),
        scope,
        "duckdb",
    )
    ids = {item.object_id for item in snapshot.objects}
    assert ids <= scope.allowed_object_ids
    assert "table.customers" not in ids
    assert {
        item.object_id for item in snapshot.ranking_evidence
    } <= scope.allowed_object_ids


def test_graph_expansion_cannot_cross_scope(resolver):
    scope = _scope({"table.transactions"})
    snapshot = resolver.resolve(
        canonicalize_question("transactions by branch"), scope, "duckdb"
    )
    assert {item.object_id for item in snapshot.objects} <= scope.allowed_object_ids
    assert snapshot.authorized_object_ids <= scope.allowed_object_ids


def test_authorized_candidates_and_expansion_stay_inside_scope(resolver):
    scope = _scope({"table.accounts", "table.branches", "relationship.account_branch"})
    candidates = resolver.authorized_candidates(scope)
    assert {item.id for item in candidates} <= scope.allowed_object_ids
    expanded = resolver.expand_authorized(("table.accounts",), scope, depth=2)
    assert set(expanded) <= scope.allowed_object_ids
    assert "table.customers" not in set(expanded)


def test_object_classification_outside_scope_is_filtered(resolver):
    scope = _scope({"table.accounts"}, allowed_classifications={"public"})
    snapshot = resolver.resolve(canonicalize_question("accounts"), scope, "duckdb")
    assert snapshot.objects == ()
    assert snapshot.authorized_object_ids == frozenset()


def test_column_classifications_are_filtered_independently_of_objects():
    table = _synthetic_table()
    retriever = SemanticRetriever(_synthetic_bundle([table]))
    resolver = GroundingResolver(retriever, retrieval_config_hash=RETRIEVAL_CONFIG_HASH)
    scope = _scope({"table.orders"}, allowed_classifications={"internal"})
    snapshot = resolver.resolve(canonicalize_question("orders"), scope, "duckdb")
    assert {item.object_id for item in snapshot.objects} == {"table.orders"}
    columns = {column.ref.column for column in snapshot.objects[0].columns}
    assert columns == {"order_id"}


def test_relationship_requires_both_authorized_endpoints(resolver):
    partial = _scope({"table.accounts", "relationship.account_branch"})
    snapshot = resolver.resolve(canonicalize_question("accounts"), partial, "duckdb")
    for item in snapshot.objects:
        assert item.relationships == ()

    full = _scope({"table.accounts", "table.branches", "relationship.account_branch"})
    snapshot = resolver.resolve(
        canonicalize_question("accounts by branch"), full, "duckdb"
    )
    relationship_ids = {
        relationship.relationship_id
        for item in snapshot.objects
        for relationship in item.relationships
    }
    assert "relationship.account_branch" in relationship_ids


# --- snapshot content is metadata only --------------------------------------


def test_snapshot_is_metadata_only_and_carries_no_database_evidence(
    resolver, account_scope
):
    snapshot = resolver.resolve(
        canonicalize_question("accounts by branch"), account_scope, "duckdb"
    )
    payload = json.loads(canonical_json_bytes(snapshot).decode("utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "database_path",
        "workshop.duckdb",
        "row_sampling",
        "embedding",
        "sample",
        "/Users/",
        "/home/",
    ):
        assert forbidden not in serialized
    assert snapshot.dialect == "duckdb"
    assert snapshot.dialect_capabilities.parameter_style == "qmark"
    assert snapshot.snapshot_version == "008.grounding.v1"
    assert snapshot.canonicalization_version == "008.question.v1"
    assert snapshot.literal_registry_version == "008.literal-span.v1"
    assert snapshot.type_registry_version == "008.types.v1"
    assert snapshot.authorization_scope_hash == account_scope.authorization_scope_hash
    assert snapshot.retrieval_config_hash == RETRIEVAL_CONFIG_HASH
    assert snapshot.policy_version == account_scope.policy_version


def test_snapshot_normalizes_declared_column_types(resolver, account_scope):
    snapshot = resolver.resolve(
        canonicalize_question("accounts"), account_scope, "duckdb"
    )
    accounts = next(
        item for item in snapshot.objects if item.object_id == "table.accounts"
    )
    types = {column.ref.column: column.data_type for column in accounts.columns}
    assert types["account_id"] == "integer"
    assert types["balance"] == "decimal"
    assert types["open_date"] == "date"
    assert types["account_type"] == "string"


def test_unknown_declared_column_type_fails_closed():
    table = _synthetic_table(
        columns=[
            {"name": "order_id", "data_type": "GEOMETRY", "classification": "internal"}
        ]
    )
    resolver = GroundingResolver(
        SemanticRetriever(_synthetic_bundle([table])),
        retrieval_config_hash=RETRIEVAL_CONFIG_HASH,
    )
    scope = _scope({"table.orders"})
    with pytest.raises(SnapshotMetadataError):
        resolver.resolve(canonicalize_question("orders"), scope, "duckdb")


def test_metric_without_declared_result_type_fails_closed():
    """A result type is governed input; it is never inferred from a formula.

    The metric is synthetic on purpose. Asserting this against the workshop
    bundle would only prove that the bundle happened to omit the declaration,
    so the guarantee would disappear the moment the bundle declared it.
    """
    metric = SemanticObject(
        id="metric.order-volume",
        type="metric",
        name="Order volume",
        description="Governed order volume.",
        cerebro={
            "classification": "internal",
            "formula": "SUM(orders.amount)",
        },
    )
    resolver = GroundingResolver(
        SemanticRetriever(_synthetic_bundle([_synthetic_table(), metric])),
        retrieval_config_hash=RETRIEVAL_CONFIG_HASH,
    )
    scope = _scope({"table.orders", "metric.order-volume"})
    with pytest.raises(SnapshotMetadataError):
        resolver.resolve(canonicalize_question("order volume"), scope, "duckdb")


def test_workshop_bundle_declares_every_metric_result_type():
    """Every governed metric card carries the declaration the resolver needs."""
    from cerebro.bundle import load_validated_bundle
    from cerebro.paths import DEFAULT_BUNDLE

    metrics = [
        obj
        for obj in load_validated_bundle(DEFAULT_BUNDLE).objects
        if obj.profile_kind == "metric"
    ]
    assert metrics
    for metric in metrics:
        assert metric.cerebro.get("metric_result_type") in {
            "string",
            "integer",
            "decimal",
            "boolean",
            "date",
            "timestamp",
        }, metric.id


def test_metric_with_declared_result_type_is_accepted():
    metric = SemanticObject(
        id="metric.order-volume",
        type="metric",
        name="Order volume",
        description="Governed order volume.",
        cerebro={
            "classification": "internal",
            "formula": "SUM(orders.amount)",
            "metric_result_type": "decimal",
        },
    )
    resolver = GroundingResolver(
        SemanticRetriever(_synthetic_bundle([_synthetic_table(), metric])),
        retrieval_config_hash=RETRIEVAL_CONFIG_HASH,
    )
    scope = _scope({"table.orders", "metric.order-volume"})
    snapshot = resolver.resolve(canonicalize_question("order volume"), scope, "duckdb")
    metric_object = next(
        item for item in snapshot.objects if item.object_id == "metric.order-volume"
    )
    assert metric_object.metric_result_type == "decimal"
    assert metric_object.formula == "SUM(orders.amount)"


def test_prose_warnings_become_informational_hashes(resolver):
    scope = _scope({"table.accounts", "table.branches", "relationship.account_branch"})
    snapshot = resolver.resolve(
        canonicalize_question("accounts by branch"), scope, "duckdb"
    )
    warnings = [warning for item in snapshot.objects for warning in item.warnings]
    assert warnings, "the bank bundle declares prose warnings"
    for warning in warnings:
        assert warning.kind == "informational"
        assert warning.control_id == ""
        assert len(warning.warning_hash) == 64
    serialized = json.dumps(json.loads(canonical_json_bytes(snapshot).decode("utf-8")))
    assert "Declared relationship" not in serialized


def test_governed_literals_are_empty_unless_authored(resolver, account_scope):
    snapshot = resolver.resolve(
        canonicalize_question("accounts by branch"), account_scope, "duckdb"
    )
    assert snapshot.governed_literals == ()


def test_authored_governed_literal_declaration_is_accepted():
    table = _synthetic_table()
    table = SemanticObject.model_validate(
        {
            **table.model_dump(),
            "cerebro": {
                **table.cerebro,
                "governed_literals": [
                    {
                        "literal_id": "literal.open-status",
                        "data_type": "string",
                        "value": "OPEN",
                    }
                ],
            },
        }
    )
    resolver = GroundingResolver(
        SemanticRetriever(_synthetic_bundle([table])),
        retrieval_config_hash=RETRIEVAL_CONFIG_HASH,
    )
    scope = _scope({"table.orders"})
    snapshot = resolver.resolve(canonicalize_question("orders"), scope, "duckdb")
    assert [literal.literal_id for literal in snapshot.governed_literals] == [
        "literal.open-status"
    ]
    literal = snapshot.governed_literals[0]
    assert literal.data_type == "string"
    assert literal.value == "OPEN"
    assert literal.source_object_id == "table.orders"


# --- snapshot identity ------------------------------------------------------


def test_snapshot_hash_is_stable_for_identical_inputs(resolver, account_scope):
    question = canonicalize_question("transaction volume")
    first = resolver.resolve(question, account_scope, "duckdb")
    second = resolver.resolve(question, account_scope, "duckdb")
    assert first.snapshot_hash == second.snapshot_hash
    assert first == second
    assert grounding_snapshot_sha256(first) == first.snapshot_hash


def test_snapshot_hash_reproduces_after_canonical_round_trip(resolver, account_scope):
    snapshot = resolver.resolve(
        canonicalize_question("accounts by branch"), account_scope, "duckdb"
    )
    reloaded = GroundingSnapshot.model_validate_json(snapshot.model_dump_json())
    assert reloaded == snapshot
    assert grounding_snapshot_sha256(reloaded) == snapshot.snapshot_hash


@pytest.mark.parametrize(
    "mutation",
    [
        "scope_objects",
        "policy_version",
        "tenant",
        "retrieval_config",
        "object_metadata",
    ],
)
def test_snapshot_identity_changes_for_security_relevant_mutation(
    retriever, account_scope, mutation
):
    question = canonicalize_question("accounts by branch")
    baseline = GroundingResolver(
        retriever, retrieval_config_hash=RETRIEVAL_CONFIG_HASH
    ).resolve(question, account_scope, "duckdb")

    changed_resolver = GroundingResolver(
        retriever, retrieval_config_hash=RETRIEVAL_CONFIG_HASH
    )
    changed_scope = account_scope
    if mutation == "scope_objects":
        changed_scope = _scope({"table.accounts"})
    elif mutation == "policy_version":
        changed_scope = _scope(
            account_scope.allowed_object_ids, policy_version="policy.v2"
        )
    elif mutation == "tenant":
        changed_scope = _scope(
            account_scope.allowed_object_ids, tenant_scope_hash="b" * 64
        )
    elif mutation == "retrieval_config":
        changed_resolver = GroundingResolver(retriever, retrieval_config_hash="d" * 64)
    else:
        mutated = retriever.bundle.model_copy(deep=True)
        for obj in mutated.objects:
            if obj.id == "table.accounts":
                obj.description = "Mutated governed description."
        changed_resolver = GroundingResolver(
            SemanticRetriever(mutated), retrieval_config_hash=RETRIEVAL_CONFIG_HASH
        )

    changed = changed_resolver.resolve(question, changed_scope, "duckdb")
    assert changed.snapshot_hash != baseline.snapshot_hash


# --- fail-closed inputs -----------------------------------------------------


def test_forged_authorization_scope_hash_is_rejected(resolver, account_scope):
    forged = account_scope.model_copy(update={"authorization_scope_hash": "f" * 64})
    with pytest.raises(AuthorizationScopeIntegrityError):
        resolver.resolve(canonicalize_question("transaction volume"), forged, "duckdb")


def test_non_canonical_question_is_rejected(resolver, account_scope):
    with pytest.raises(ValueError):
        resolver.resolve("  Accounts   by  branch ", account_scope, "duckdb")


def test_only_duckdb_dialect_is_accepted(resolver, account_scope):
    for dialect in ("postgres", "DuckDB", "", "sqlite"):
        with pytest.raises(UnsupportedDialectError):
            resolver.resolve(canonicalize_question("accounts"), account_scope, dialect)


# --- advisory boundary stays advisory ---------------------------------------


def test_advisory_grounding_cannot_authorize_sql(retriever, account_scope):
    advisory = retriever.grounding("total transaction amount by branch")
    with pytest.raises(ValidationError):
        SQLGenerationRequest.model_validate(
            {
                "question": "total transaction amount by branch",
                "authorization_scope": account_scope.model_dump(mode="json"),
                "grounding": advisory.model_dump(mode="json"),
            }
        )
    request = SQLGenerationRequest.model_validate(
        {
            "question": "total transaction amount by branch",
            "authorization_scope": account_scope.model_dump(mode="json"),
        }
    )
    assert request.dialect == "duckdb"


def test_legacy_advisory_retrieval_methods_are_preserved(retriever):
    assert retriever.search("fraud rate by card type", 10)
    assert "table.card_transactions" in retriever.expand(
        ["entity.card-transaction"], depth=1
    )
    advisory = retriever.grounding("total transaction amount by branch")
    assert advisory.retrieval_mode in {"lexical_graph", "hybrid_graph"}
    assert advisory.question == "total transaction amount by branch"


def test_api_module_exposes_no_sql_generation_endpoint():
    from pathlib import Path

    from cerebro import api

    source = Path(api.__file__).read_text(encoding="utf-8")
    assert "SQLGenerationRequest" not in source
    assert "Text2SQLAgent" not in source
    assert "/api/sql" not in source
