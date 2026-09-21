import json
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
    assert sum(item.profile_kind == "relationship" for item in bundle.objects) == 11
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
    events: list[tuple[str, str, dict]] = []
    result = run_generation_workflow(
        bank_source_config,
        output=tmp_path / "workflow-candidate",
        provider=None,
        on_stage=lambda stage, status, _summary, details: events.append((stage, status, details)),
    )

    completed = [stage for stage, status, _details in events if status == "completed"]
    skipped = [stage for stage, status, _details in events if status == "skipped"]
    assert completed == [
        "source_check",
        "catalog_scan",
        "compile_okf",
        "validate_candidate",
        "candidate_ready",
    ]
    assert skipped == ["business_semantics", "relationship_semantics", "query_semantics"]
    assert [details["agent_id"] for stage, status, details in events if status == "skipped"] == [
        "semantic_inventory", "relationship", "metric_rule"
    ]
    assert result.bundle.review_state == "candidate"
    assert result.proposal.generation_mode == "fallback"


def test_generation_trace_exposes_sanitized_typed_agent_io(bank_source_config, tmp_path: Path):
    fallback = SemanticEnricher().fallback()

    class TypedProvider:
        name = "trace-provider"
        model = "trace-model"

        def generate(self, _schema_name, _prompt, output_model):
            if output_model is BusinessSemantics:
                return fallback.business
            if output_model is RelationshipSemantics:
                return fallback.relationships
            if output_model is QuerySemantics:
                return fallback.query
            raise AssertionError(output_model)

    trace: list[tuple[str, str, dict | None, dict | None]] = []
    run_generation_workflow(
        bank_source_config,
        output=tmp_path / "traced-candidate",
        provider=TypedProvider(),
        on_trace=lambda stage, status, input_payload, output_payload: trace.append(
            (stage, status, input_payload, output_payload)
        ),
    )

    started = {stage: input_payload for stage, status, input_payload, _output in trace if status == "started"}
    completed = {stage: output for stage, status, _input, output in trace if status == "completed"}
    assert started["business_semantics"]["catalog"]["source_name"] == "bank-workshop"
    assert "semantic_inventory" in started["relationship_semantics"]
    assert "relationships" in started["query_semantics"]
    assert completed["business_semantics"] == fallback.business.model_dump(mode="json")
    assert completed["relationship_semantics"] == fallback.relationships.model_dump(mode="json")
    assert completed["query_semantics"] == fallback.query.model_dump(mode="json")
    serialized = json.dumps(trace)
    assert "database_path" not in serialized
    assert str(bank_source_config) not in serialized


def _connected_proposal() -> SemanticProposal:
    return SemanticProposal(
        business=BusinessSemantics(
            table_purposes={},
            classifications={},
            domains=[{
                "id": "retail-banking", "name": "Retail Banking",
                "description": "Accounts and transactions for retail customers.",
                "classification": "internal", "owner": "Retail Banking", "warnings": [],
            }],
            entities=[
                {
                    "id": "account", "name": "Account", "description": "A banking account.",
                    "aliases": [], "classification": "confidential",
                    "physical_mapping": {"table": "accounts", "key": ["account_id"]},
                    "grain": {"type": "entity", "description": "One account", "key": ["account_id"]},
                    "domain": "retail-banking", "warnings": [],
                },
                {
                    "id": "transaction", "name": "Transaction", "description": "A posted transaction.",
                    "aliases": [], "classification": "confidential",
                    "physical_mapping": {"table": "transactions", "key": ["transaction_id"]},
                    "grain": {"type": "event", "description": "One transaction", "key": ["transaction_id"]},
                    "domain": "retail-banking", "warnings": [],
                },
            ],
            dimensions=[{
                "id": "transaction-channel", "name": "Transaction channel",
                "description": "Channel used for a transaction.", "entity": "transaction",
                "physical_mappings": [{"table": "transactions", "column": "channel"}],
                "semantic_type": "categorical", "compatible_metrics": ["transaction-volume"],
                "classification": "internal", "warnings": [],
            }],
            policies=[{
                "id": "customer-data-handling", "name": "Customer data handling",
                "description": "Protect likely customer identity fields.", "classification": "restricted",
                "applies_to": ["customers"], "rule": "Aggregate and minimize identity fields.",
                "confidence": 0.9, "evidence": ["customers.email"], "warnings": ["Human review required."],
            }],
        ),
        relationships=RelationshipSemantics(relationships=[{
            "id": "transaction-account", "source_table": "transactions", "source_column": "account_id",
            "target_table": "accounts", "target_column": "account_id", "cardinality": "many-to-one",
            "source_entity": "transaction", "target_entity": "account",
            "description": "Each transaction belongs to an account.", "confidence": 0.9,
            "evidence": ["transactions.account_id", "accounts.account_id"],
        }]),
        query=QuerySemantics(
            grains={}, dimensions=[], joins=[], guidance=[], warnings=[],
            structured_measures=[{
                "id": "transaction-volume", "name": "Transaction volume",
                "description": "Total transaction amount.", "classification": "confidential",
                "entity": "transaction",
                "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "transactions", "column": "amount"}},
                "dependencies": ["transactions"],
                "grain": {"type": "aggregate", "description": "Requested compatible dimensions"},
                "compatible_dimensions": ["transaction-channel"], "warnings": [],
            }],
            rules=[{
                "id": "channel-present", "name": "Channel present", "description": "Transaction has a channel.",
                "entity": "transaction", "rule_kind": "predicate", "output_type": "boolean",
                "dependencies": ["transaction-channel"], "logic": "transaction channel is not null",
                "grain": {"type": "event", "description": "One transaction"},
                "classification": "internal", "warnings": [],
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
    candidate_root = compile_candidate_bundle(snapshot, _connected_proposal(), tmp_path / "connected")
    bundle = load_validated_bundle(candidate_root)
    by_id = bundle.by_id()
    assert by_id["entity.account"].cerebro["physical_mapping"] == {
        "table": "table.accounts", "key": ["account_id"]
    }
    assert by_id["entity.account"].cerebro["domain"] == "domain.retail-banking"
    assert by_id["entity.account"].links == ["table.accounts", "domain.retail-banking"]
    domain = by_id["domain.retail-banking"]
    assert domain.profile_kind == "domain"
    assert domain.cerebro["classification"] == "internal"
    assert domain.cerebro["owner"] == "Retail Banking"
    assert sum(item.profile_kind == "domain" for item in bundle.objects) == 1
    assert (candidate_root / "domains" / "index.md").is_file()
    assert by_id["dimension.transaction-channel"].cerebro["entity"] == "entity.transaction"
    assert by_id["metric.transaction-volume"].cerebro["dependencies"] == ["table.transactions"]
    assert by_id["metric.transaction-volume"].cerebro["formula"] == "SUM(transactions.amount)"
    assert by_id["rule.channel-present"].cerebro["dependencies"] == ["dimension.transaction-channel"]
    assert by_id["relationship.transaction-account"].cerebro["semantic"] == {
        "from": "entity.transaction", "to": "entity.account"
    }
    policy = by_id["policy.customer-data-handling"]
    assert policy.provenance["origin"] == "ai_proposed"
    assert policy.cerebro["applies_to"] == ["table.customers"]
    assert policy.cerebro["confidence"] == 0.9
    assert policy.cerebro["evidence"] == ["customers.email"]
    customers = by_id["table.customers"]
    assert all(column["classification"] == "internal" for column in customers.cerebro["columns"])


def test_compiler_canonicalizes_underscore_and_prefixed_semantic_ids(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.domains[0].id = "domain.retail_banking"
    proposal.business.entities[0].id = "entity_account"
    proposal.business.entities[0].domain = "retail_banking"
    proposal.business.entities[1].id = "entity.entity_transaction"
    proposal.business.entities[1].domain = "domain.retail_banking"
    proposal.business.dimensions[0].id = "dimension.transaction_channel"
    proposal.business.dimensions[0].entity = "entity_transaction"
    proposal.business.dimensions[0].compatible_metrics = ["metric.transaction_volume"]
    proposal.query.structured_measures[0].id = "transaction_volume"
    proposal.query.structured_measures[0].entity = "entity.entity_transaction"
    proposal.query.structured_measures[0].compatible_dimensions = ["transaction_channel"]
    proposal.query.rules[0].id = "rule.channel_present"
    proposal.query.rules[0].entity = "entity_transaction"
    proposal.query.rules[0].dependencies = ["dimension.transaction_channel"]
    proposal.relationships.relationships[0].id = "relationship.transaction_account"
    proposal.relationships.relationships[0].source_entity = "entity_transaction"
    proposal.relationships.relationships[0].target_entity = "entity_account"

    bundle = load_validated_bundle(
        compile_candidate_bundle(snapshot, proposal, tmp_path / "canonical-ids")
    )
    by_id = bundle.by_id()

    assert "entity.entity-account" in by_id
    assert "entity.entity-transaction" in by_id
    assert "domain.retail-banking" in by_id
    assert by_id["entity.entity-account"].cerebro["domain"] == "domain.retail-banking"
    assert by_id["entity.entity-transaction"].cerebro["domain"] == "domain.retail-banking"
    assert by_id["dimension.transaction-channel"].cerebro["entity"] == "entity.entity-transaction"
    assert by_id["dimension.transaction-channel"].cerebro["compatible_metrics"] == [
        "metric.transaction-volume"
    ]
    assert by_id["metric.transaction-volume"].cerebro["compatible_dimensions"] == [
        "dimension.transaction-channel"
    ]
    assert by_id["rule.channel-present"].cerebro["dependencies"] == [
        "dimension.transaction-channel"
    ]
    assert by_id["relationship.transaction-account"].cerebro["semantic"] == {
        "from": "entity.entity-transaction",
        "to": "entity.entity-account",
    }


def test_compiler_allows_empty_query_semantics_and_drops_forward_metric_hints(
    bank_database, tmp_path: Path
):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.query = QuerySemantics()

    bundle = load_validated_bundle(
        compile_candidate_bundle(snapshot, proposal, tmp_path / "empty-query")
    )
    by_id = bundle.by_id()
    dimension = by_id["dimension.transaction-channel"]

    assert not any(item.profile_kind in {"metric", "business_rule"} for item in bundle.objects)
    assert dimension.cerebro["compatible_metrics"] == []
    assert dimension.links == ["entity.transaction", "table.transactions"]
    assert any("unconfirmed metric compatibility" in warning for warning in dimension.cerebro["warnings"])


def test_empty_query_reconciliation_is_visible_in_generation_trace(bank_database, tmp_path: Path):
    proposal = _connected_proposal()

    class EmptyQueryProvider:
        name = "empty-query-provider"
        model = "typed-fixture"

        def generate(self, _schema_name, _prompt, output_model):
            if output_model is BusinessSemantics:
                return proposal.business
            if output_model is RelationshipSemantics:
                return proposal.relationships
            if output_model is QuerySemantics:
                return QuerySemantics()
            raise AssertionError(output_model)

    trace: list[tuple[str, str, dict | None, dict | None]] = []
    result = run_generation_workflow(
        tmp_path / "unused.yaml",
        database_path=bank_database,
        output=tmp_path / "empty-query-workflow",
        provider=EmptyQueryProvider(),
        source_mode="database_only",
        database_schema="main",
        on_trace=lambda stage, status, input_payload, output_payload: trace.append(
            (stage, status, input_payload, output_payload)
        ),
    )

    compile_output = next(
        output_payload
        for stage, status, _input_payload, output_payload in trace
        if stage == "compile_okf" and status == "completed"
    )
    assert result.proposal.generation_mode == "live"
    assert compile_output is not None
    assert compile_output["empty_categories"] == ["metric", "business_rule"]
    assert any(
        "unconfirmed metric compatibility" in warning
        for warning in compile_output["linker_warnings"]
    )


def test_relationship_agent_enriches_matching_declared_physical_edge(bank_source_config, tmp_path: Path):
    bundle = load_validated_bundle(compile_candidate_bundle(
        DuckDBSource(bank_source_config).scan(),
        _connected_proposal(),
        tmp_path / "declared-relationship-enrichment",
    ))
    relationship = next(
        item for item in bundle.objects
        if item.profile_kind == "relationship"
        and item.cerebro["source_table"] == "table.transactions"
        and item.cerebro["target_table"] == "table.accounts"
    )
    assert relationship.cerebro["semantic"] == {
        "from": "entity.transaction", "to": "entity.account"
    }
    assert relationship.cerebro["semantic_provenance"] == "ai_proposed"
    assert relationship.provenance["origin"] == "declared"


def test_compiler_rejects_invented_semantic_targets(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.entities[0].physical_mapping.table = "missing_table"
    with pytest.raises(ValueError, match="invalid_entity_table"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "invalid-semantic-target")


def test_compiler_still_rejects_unknown_metric_dimensions(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.query.structured_measures[0].compatible_dimensions = ["missing-dimension"]
    with pytest.raises(ValueError, match="invalid_metric_dimension"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "invalid-metric-dimension")


def test_compiler_rejects_duplicate_semantic_ids(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.entities.append(proposal.business.entities[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="duplicate_semantic_id"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "duplicate-semantic-id")


def test_compiler_rejects_unresolved_entity_domain_reference(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.entities[0].domain = "missing-domain"
    with pytest.raises(ValueError, match="invalid_entity_domain_reference"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "invalid-entity-domain")


def test_compiler_rejects_duplicate_domain_ids(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.domains.append(proposal.business.domains[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="duplicate_semantic_id"):
        compile_candidate_bundle(snapshot, proposal, tmp_path / "duplicate-domain-id")


def test_entity_without_a_domain_still_compiles(bank_database, tmp_path: Path):
    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", bank_database, source_mode="database_only", schema="main"
    ).scan()
    proposal = _connected_proposal()
    proposal.business.entities[0].domain = None
    bundle = load_validated_bundle(
        compile_candidate_bundle(snapshot, proposal, tmp_path / "domain-less-entity")
    )
    account = bundle.by_id()["entity.account"]
    assert account.links == ["table.accounts"]
    assert account.cerebro.get("domain") is None
