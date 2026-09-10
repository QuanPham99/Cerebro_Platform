import asyncio
from pathlib import Path

import httpx
import pytest

from cerebro.api import create_app
from cerebro.bundle import load_validated_bundle
from cerebro.definitions import DefinitionRevisionManager, DefinitionValidationError
from cerebro.generation import bundle_digest
from cerebro.models import DefinitionApplyRequest, DefinitionTranslateRequest, MetricDefinitionPayload, ReviewRequest
from cerebro.paths import DEFAULT_BUNDLE


def metric_request(column: str = "account_id") -> DefinitionApplyRequest:
    return DefinitionApplyRequest.model_validate({
        "origin": "declared",
        "payload": {
            "kind": "metric",
            "definition": {
                "id": "account-count",
                "name": "Account Count",
                "description": "Distinct count of accounts.",
                "entity": "entity.account",
                "measure": {
                    "kind": "aggregate",
                    "aggregation": "count_distinct",
                    "source": {"table": "table.accounts", "column": column},
                },
                "dependencies": ["table.accounts"],
                "grain": {"type": "aggregate", "description": "Requested compatible dimensions"},
                "compatible_dimensions": ["dimension.account-type"],
                "classification": "confidential",
                "warnings": [],
            },
        },
    })


def rule_request() -> DefinitionApplyRequest:
    return DefinitionApplyRequest.model_validate({
        "origin": "declared",
        "payload": {
            "kind": "business_rule",
            "definition": {
                "id": "positive-account-balance",
                "name": "Positive Account Balance",
                "description": "An account has a positive current balance.",
                "entity": "entity.account",
                "rule_kind": "predicate",
                "output_type": "boolean",
                "dependencies": ["table.accounts"],
                "logic": "accounts.balance > 0",
                "grain": {"type": "entity", "description": "One account"},
                "classification": "confidential",
                "warnings": [],
            },
        },
    })


def test_definition_revision_forks_active_bundle_and_updates_graph(tmp_path: Path):
    golden = load_validated_bundle(DEFAULT_BUNDLE)
    before = bundle_digest(DEFAULT_BUNDLE)
    manager = DefinitionRevisionManager(
        active_bundle=lambda: golden,
        provider_factory=lambda: None,
        output_root=tmp_path / "generated",
        reviewed_root=tmp_path / "reviewed",
    )

    revision = manager.create(metric_request())

    assert revision.generation_mode == "authored"
    assert revision.review_state == "candidate"
    assert revision.base_version == "0.2.0"
    assert bundle_digest(DEFAULT_BUNDLE) == before
    graph = manager.graph(revision.id)
    assert any(node["id"] == "metric.account-count" for node in graph["nodes"])
    edges = {(edge["source"], edge["target"], edge["type"]) for edge in graph["edges"]}
    assert ("metric.account-count", "entity.account", "metric_entity") in edges
    assert ("metric.account-count", "dimension.account-type", "metric_dimension") in edges
    dimension = manager.object(revision.id, "dimension.account-type")
    assert dimension is not None
    assert "metric.account-count" in dimension.cerebro["compatible_metrics"]

    updated = manager.add(revision.id, rule_request())
    assert updated.counts["business_rule"] >= 1
    assert manager.object(revision.id, "rule.positive-account-balance") is not None


def test_definition_revision_rejects_unknown_physical_columns(tmp_path: Path):
    manager = DefinitionRevisionManager(
        active_bundle=lambda: load_validated_bundle(DEFAULT_BUNDLE),
        provider_factory=lambda: None,
        output_root=tmp_path / "generated",
        reviewed_root=tmp_path / "reviewed",
    )

    with pytest.raises(DefinitionValidationError, match="invalid column"):
        manager.create(metric_request("missing_column"))


def test_definition_api_preserves_golden_and_exposes_draft_graph(tmp_path: Path):
    before = bundle_digest(DEFAULT_BUNDLE)
    app = create_app(
        bundle_path=DEFAULT_BUNDLE,
        generation_output_root=tmp_path / "generated",
        reviewed_output_root=tmp_path / "reviewed",
    )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            golden = await client.get("/api/bundles/golden")
            assert golden.status_code == 200
            assert golden.json()["version"] == "0.2.0"
            context = await client.get("/api/definitions/context")
            assert context.status_code == 200
            assert any(item["id"] == "entity.account" for item in context.json()["entities"])

            created = await client.post(
                "/api/definition-revisions",
                json=metric_request().model_dump(mode="json"),
            )
            assert created.status_code == 201
            revision_id = created.json()["id"]
            graph = await client.get(f"/api/definition-revisions/{revision_id}/graph")
            assert graph.status_code == 200
            assert any(node["id"] == "metric.account-count" for node in graph.json()["nodes"])
            authored = await client.get(
                f"/api/definition-revisions/{revision_id}/objects/metric.account-count"
            )
            assert authored.status_code == 200
            assert authored.json()["profile_kind"] == "metric"

    asyncio.run(exercise())
    assert bundle_digest(DEFAULT_BUNDLE) == before


def test_definition_api_forks_any_trusted_approved_version(tmp_path: Path):
    generated = tmp_path / "generated"
    reviewed = tmp_path / "reviewed"
    before = bundle_digest(DEFAULT_BUNDLE)
    app = create_app(
        bundle_path=DEFAULT_BUNDLE,
        generation_output_root=generated,
        reviewed_output_root=reviewed,
    )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first_payload = metric_request().model_dump(mode="json")
            first_payload["base_bundle_id"] = "golden"
            first = await client.post("/api/definition-revisions", json=first_payload)
            assert first.status_code == 201
            first_revision = first.json()
            assert first_revision["base_bundle_id"] == "golden"
            assert first_revision["definitions"] == [
                {"id": "metric.account-count", "name": "Account Count", "kind": "metric"}
            ]

            draft_context = await client.get(
                "/api/definitions/context",
                params={"revision_id": first_revision["id"]},
            )
            assert draft_context.status_code == 200
            assert any(item["id"] == "metric.account-count" for item in draft_context.json()["metrics"])

            approval = await client.post(
                f"/api/definition-revisions/{first_revision['id']}/reviews",
                json={
                    "decision": "approve",
                    "reviewer": "Data Owner",
                    "comment": "Metric reviewed.",
                    "acknowledge_ai_risk": True,
                },
            )
            assert approval.status_code == 200

            saved_context = await client.get(
                "/api/definitions/context",
                params={"base_bundle_id": first_revision["id"]},
            )
            assert saved_context.status_code == 200
            assert saved_context.json()["bundle_id"] == first_revision["id"]

            second_payload = rule_request().model_dump(mode="json")
            second_payload["base_bundle_id"] = first_revision["id"]
            second = await client.post("/api/definition-revisions", json=second_payload)
            assert second.status_code == 201
            assert second.json()["base_bundle_id"] == first_revision["id"]
            assert second.json()["base_version"] == first_revision["version"]

            before_unknown = sorted(path.name for path in generated.iterdir())
            unknown_payload = rule_request().model_dump(mode="json")
            unknown_payload["base_bundle_id"] = "missing-version"
            unknown = await client.post("/api/definition-revisions", json=unknown_payload)
            assert unknown.status_code == 404
            assert sorted(path.name for path in generated.iterdir()) == before_unknown

    asyncio.run(exercise())
    assert bundle_digest(DEFAULT_BUNDLE) == before


def test_definition_translation_uses_typed_approved_graph_context(tmp_path: Path):
    requested_models: list[type] = []
    prompts: list[str] = []

    class Provider:
        name = "typed-definition-provider"
        model = "fixture-model"

        def generate(self, _schema_name: str, prompt: str, output_model: type):
            prompts.append(prompt)
            requested_models.append(output_model)
            return MetricDefinitionPayload.model_validate(metric_request().payload.model_dump(mode="json"))

    manager = DefinitionRevisionManager(
        active_bundle=lambda: load_validated_bundle(DEFAULT_BUNDLE),
        provider_factory=Provider,
        output_root=tmp_path / "generated",
        reviewed_root=tmp_path / "reviewed",
    )

    translated = manager.translate(DefinitionTranslateRequest(
        kind="metric",
        intent="Count distinct accounts by account type",
        entity_id="entity.account",
    ))

    assert translated.payload.kind == "metric"
    assert translated.payload.definition.id == "metric.account-count"
    assert requested_models == [MetricDefinitionPayload]
    assert "entity.account" in prompts[0]
    assert "table.accounts" in prompts[0]


def test_definition_review_state_survives_manager_restart(tmp_path: Path):
    output_root = tmp_path / "generated"
    reviewed_root = tmp_path / "reviewed"
    manager = DefinitionRevisionManager(
        active_bundle=lambda: load_validated_bundle(DEFAULT_BUNDLE),
        provider_factory=lambda: None,
        output_root=output_root,
        reviewed_root=reviewed_root,
    )
    revision = manager.create(metric_request())
    manager.review(revision.id, ReviewRequest(
        decision="approve",
        reviewer="Data Owner",
        comment="Definition checked against the approved graph.",
        acknowledge_ai_risk=True,
    ))

    restarted = DefinitionRevisionManager(
        active_bundle=lambda: load_validated_bundle(DEFAULT_BUNDLE),
        provider_factory=lambda: None,
        output_root=output_root,
        reviewed_root=reviewed_root,
    )

    restored = restarted.get(revision.id)
    assert restored.review_state == "approved"
    assert restored.review_record is not None
    assert restored.review_record["reviewer"] == "Data Owner"
