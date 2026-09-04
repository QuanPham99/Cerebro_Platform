from pathlib import Path

import pytest

import cerebro.generation as generation
from cerebro.bundle import load_validated_bundle
from cerebro.enrichment import SemanticEnricher
from cerebro.generation import activate_bundle, compile_candidate_bundle, review_bundle, run_generation_workflow
from cerebro.models import (
    BusinessSemantics,
    QuerySemantics,
    RelationshipCandidate,
    RelationshipSemantics,
    SemanticProposal,
)
from cerebro.source import DuckDBSource


def test_fallback_compiles_valid_separate_candidate(bank_source_config, tmp_path: Path):
    snapshot = DuckDBSource(bank_source_config).scan()
    output = tmp_path / "candidate"
    compiled = compile_candidate_bundle(snapshot, SemanticEnricher().fallback(), output)
    bundle = load_validated_bundle(compiled)
    assert bundle.review_state == "candidate"
    assert bundle.generation_mode == "fallback"
    assert sum(item.type == "relationship" for item in bundle.objects) == 11
    assert (output / "bundle.yaml").is_file()
    with pytest.raises(FileExistsError):
        compile_candidate_bundle(snapshot, SemanticEnricher().fallback(), output)


def test_activation_requires_a_valid_bundle(bank_source_config, tmp_path: Path, monkeypatch):
    candidate = compile_candidate_bundle(
        DuckDBSource(bank_source_config).scan(), SemanticEnricher().fallback(), tmp_path / "candidate"
    )
    record = review_bundle(
        candidate,
        reviewer="Test reviewer",
        decision="approve",
        acknowledge_ai_risk=True,
        reviewed_root=tmp_path / "reviewed",
    )
    pointer = tmp_path / "active.json"
    monkeypatch.setattr(generation, "ACTIVE_BUNDLE_POINTER", pointer)
    payload = activate_bundle(record.reviewed_bundle)
    assert payload["path"] == record.reviewed_bundle
    assert pointer.is_file()


def test_candidate_rejects_hallucinated_relationship_endpoints(bank_source_config, tmp_path: Path):
    snapshot = DuckDBSource(bank_source_config).scan()
    proposal = SemanticEnricher().fallback()
    proposal.relationships.relationships.append(RelationshipCandidate(
        id="invented",
        source_table="customers",
        source_column="customer_id",
        target_table="missing_table",
        target_column="customer_id",
        cardinality="many-to-one",
    ))
    with pytest.raises(ValueError, match="unknown table or column"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "invalid-candidate")


def test_shared_generation_workflow_emits_ordered_fallback_stages(bank_source_config, tmp_path: Path):
    events: list[tuple[str, str]] = []
    result = run_generation_workflow(
        bank_source_config,
        output=tmp_path / "workflow-candidate",
        provider=None,
        on_stage=lambda stage, status, _summary, _details: events.append((stage, status)),
    )

    completed = [stage for stage, status in events if status == "completed"]
    skipped = [stage for stage, status in events if status == "skipped"]
    assert completed == [
        "source_check",
        "catalog_scan",
        "compile_okf",
        "validate_candidate",
        "candidate_ready",
    ]
    assert skipped == ["business_semantics", "relationship_semantics", "query_semantics"]
    assert result.bundle.review_state == "candidate"
    assert result.proposal.generation_mode == "fallback"


def _connected_proposal() -> SemanticProposal:
    return SemanticProposal(
        business=BusinessSemantics(
            table_purposes={},
            classifications={},
            concepts=[{
                "id": "account", "name": "Account", "description": "A banking account.",
                "aliases": [], "classification": "internal", "maps_to": ["accounts"], "warnings": [],
            }],
            policies=[{
                "id": "customer-data-handling", "name": "Customer data handling",
                "description": "Protect likely customer identity fields.", "classification": "restricted",
                "applies_to": ["customers"], "rule": "Aggregate and minimize identity fields.",
                "confidence": 0.9, "evidence": ["customers.email"], "warnings": ["Human review required."],
            }],
        ),
        relationships=RelationshipSemantics(),
        query=QuerySemantics(
            grains={}, dimensions=[], joins=[], guidance=[], warnings=[],
            measures=[{
                "id": "transaction-volume", "name": "Transaction volume",
                "description": "Total transaction amount.", "classification": "confidential",
                "formula": "SUM(transactions.amount)", "dependencies": ["transactions"],
                "filters": [], "grain": "aggregate over transactions", "warnings": [],
            }],
        ),
        generation_mode="live",
        provider="deterministic-test-provider",
        model="typed-fixture",
    )


def test_compiler_preserves_connected_ai_semantics(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    bundle = load_validated_bundle(
        compile_candidate_bundle(snapshot, _connected_proposal(), tmp_path / "connected")
    )
    by_id = bundle.by_id()
    assert by_id["concept.account"].cerebro["maps_to"] == ["table.accounts"]
    assert by_id["metric.transaction-volume"].cerebro["dependencies"] == ["table.transactions"]
    policy = by_id["policy.customer-data-handling"]
    assert policy.provenance["origin"] == "ai_proposed"
    assert policy.cerebro["applies_to"] == ["table.customers"]
    assert policy.cerebro["confidence"] == 0.9
    assert policy.cerebro["evidence"] == ["customers.email"]
    customers = by_id["table.customers"]
    assert all(column["classification"] == "internal" for column in customers.cerebro["columns"])


def test_compiler_rejects_invented_semantic_targets(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.concepts[0].maps_to = ["missing_table"]
    with pytest.raises(ValueError, match="invalid_concept_mapping_target"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "invalid-semantic-target")


def test_compiler_rejects_duplicate_semantic_ids(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.concepts.append(proposal.business.concepts[0].model_copy())
    with pytest.raises(ValueError, match="duplicate_semantic_id"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "duplicate-semantic-id")
