import pytest
from pydantic import ValidationError

from cerebro.enrichment import GenerationProvider, SemanticEnricher
from cerebro.models import (
    BusinessSemantics,
    ConceptCandidate,
    MetricCandidate,
    PolicyCandidate,
    QuerySemantics,
    RelationshipSemantics,
)
from cerebro.source import DuckDBSource
from cerebro.semantic.agents import MetricRuleAgent, RelationshipAgent, SemanticInventoryAgent


class MockProvider(GenerationProvider):
    name = "mock"
    model = "semantic-fixture"

    def __init__(self):
        self.calls = []

    def generate(self, schema_name, prompt, output_model):
        self.calls.append((schema_name, prompt, output_model))
        if output_model is BusinessSemantics:
            return BusinessSemantics(
                table_purposes={"accounts": "Accounts"},
                entities=[{
                    "id": "transaction", "name": "Transaction", "description": "A posted transaction.",
                    "aliases": [], "classification": "confidential",
                    "physical_mapping": {"table": "transactions", "key": ["transaction_id"]},
                    "grain": {"type": "event", "description": "One transaction", "key": ["transaction_id"]},
                    "warnings": [],
                }],
                dimensions=[{
                    "id": "transaction-channel", "name": "Transaction channel",
                    "description": "Channel used for the transaction.", "entity": "transaction",
                    "physical_mappings": [{"table": "transactions", "column": "channel"}],
                    "semantic_type": "categorical", "compatible_metrics": ["transaction-volume"],
                    "classification": "internal", "warnings": [],
                }],
                policies=[{
                    "id": "customer-data-handling",
                    "name": "Customer data handling",
                    "description": "Protect customer identity fields.",
                    "classification": "restricted",
                    "applies_to": ["customers"],
                    "rule": "Return aggregate results and minimize identity fields.",
                    "confidence": 0.9,
                    "evidence": ["customers.email"],
                    "warnings": ["Requires human review."],
                }],
                classifications={"accounts.balance": "confidential"},
            )
        if output_model is RelationshipSemantics:
            return RelationshipSemantics()
        return QuerySemantics(
            grains={"accounts": "one account"},
            dimensions=[],
            structured_measures=[{
                "id": "transaction-volume",
                "name": "Transaction volume",
                "description": "Total transaction amount.",
                "classification": "confidential",
                "entity": "transaction",
                "measure": {"kind": "aggregate", "aggregation": "sum", "source": {"table": "transactions", "column": "amount"}},
                "dependencies": ["transactions"],
                "grain": {"type": "aggregate", "description": "Requested dimensions"},
                "compatible_dimensions": ["transaction-channel"],
                "warnings": [],
            }],
            rules=[{
                "id": "channel-present", "name": "Channel present", "description": "Transaction has a channel.",
                "entity": "transaction", "rule_kind": "predicate", "output_type": "boolean",
                "dependencies": ["transaction-channel"], "logic": "transaction channel is not null",
                "grain": {"type": "event", "description": "One transaction"},
                "classification": "internal", "warnings": [],
            }],
            joins=[],
            guidance=["Use declared joins"],
            warnings=[],
        )


def test_three_structured_enrichment_stages_are_catalog_only(bank_source_config):
    provider = MockProvider()
    proposal = SemanticEnricher(provider).enrich(DuckDBSource(bank_source_config).scan())
    assert proposal.generation_mode == "live"
    assert [call[0] for call in provider.calls] == [
        "business_semantics", "relationship_semantics", "query_semantics"
    ]
    prompts = "\n".join(call[1] for call in provider.calls)
    assert "row_sampling\"" in prompts
    assert '"disabled"' in prompts
    assert "Pooja Garcia" not in prompts
    assert "customer0@mailbank.com" not in prompts
    assert len(proposal.business.policies) == 1
    assert len(proposal.business.entities) == 1
    assert len(proposal.business.dimensions) == 1
    assert len(proposal.query.structured_measures) == 1
    assert len(proposal.query.rules) == 1


def test_structural_smoke_skips_metric_rule_provider_call(bank_source_config):
    provider = MockProvider()
    events = []
    proposal = SemanticEnricher(provider).enrich(
        DuckDBSource(bank_source_config).scan(),
        include_query_semantics=False,
        on_stage=lambda stage, status, summary, details: events.append(
            (stage, status, summary, details)
        ),
    )

    assert proposal.generation_mode == "live"
    assert [call[0] for call in provider.calls] == ["business_semantics", "relationship_semantics"]
    assert proposal.query.structured_measures == []
    assert proposal.query.rules == []
    skipped = [event for event in events if event[0] == "query_semantics"]
    assert skipped == [(
        "query_semantics",
        "skipped",
        "Skipped metrics and business rules; they are authored after graph activation.",
        {"agent_id": "metric_rule", "reason": "post_activation_authoring"},
    )]


def test_semantic_inventory_agent_makes_one_typed_catalog_only_call(bank_source_config):
    provider = MockProvider()
    snapshot = DuckDBSource(bank_source_config).scan()

    result = SemanticInventoryAgent(provider).run(snapshot)

    assert isinstance(result, BusinessSemantics)
    assert len(provider.calls) == 1
    schema_name, prompt, output_model = provider.calls[0]
    assert (schema_name, output_model) == ("business_semantics", BusinessSemantics)
    assert "SemanticInventoryAgent" in prompt
    assert "accounts" in prompt
    assert "Set compatible_metrics to an empty list" in prompt
    assert str(snapshot.database_path) not in prompt
    assert "database_path" not in prompt


def test_relationship_agent_receives_inventory_and_makes_one_typed_call(bank_source_config):
    provider = MockProvider()
    snapshot = DuckDBSource(bank_source_config).scan()
    inventory = MockProvider().generate("business_semantics", "", BusinessSemantics)

    result = RelationshipAgent(provider).run(snapshot, inventory)

    assert isinstance(result, RelationshipSemantics)
    assert len(provider.calls) == 1
    schema_name, prompt, output_model = provider.calls[0]
    assert (schema_name, output_model) == ("relationship_semantics", RelationshipSemantics)
    assert "RelationshipAgent" in prompt
    assert '"id": "transaction"' in prompt
    assert str(snapshot.database_path) not in prompt


def test_metric_rule_agent_receives_upstream_context_and_makes_one_typed_call(bank_source_config):
    provider = MockProvider()
    snapshot = DuckDBSource(bank_source_config).scan()
    fixture = MockProvider()
    inventory = fixture.generate("business_semantics", "", BusinessSemantics)
    relationships = RelationshipSemantics(relationships=[{
        "id": "transaction-account",
        "source_table": "transactions",
        "source_column": "account_id",
        "target_table": "accounts",
        "target_column": "account_id",
        "cardinality": "many-to-one",
        "source_entity": "transaction",
        "target_entity": "account",
    }])

    result = MetricRuleAgent(provider).run(snapshot, inventory, relationships)

    assert isinstance(result, QuerySemantics)
    assert len(provider.calls) == 1
    schema_name, prompt, output_model = provider.calls[0]
    assert (schema_name, output_model) == ("query_semantics", QuerySemantics)
    assert "MetricRuleAgent" in prompt
    assert '"id": "transaction"' in prompt
    assert '"id": "transaction-account"' in prompt
    assert str(snapshot.database_path) not in prompt


@pytest.mark.parametrize(
    ("agent_factory", "args", "output_model"),
    [
        (SemanticInventoryAgent, (), BusinessSemantics),
        (RelationshipAgent, (BusinessSemantics(),), RelationshipSemantics),
        (MetricRuleAgent, (BusinessSemantics(), RelationshipSemantics()), QuerySemantics),
    ],
)
def test_agents_preserve_supported_empty_typed_results(
    bank_source_config, agent_factory, args, output_model
):
    class EmptyProvider(GenerationProvider):
        name = "empty"
        model = "empty-fixture"

        def __init__(self):
            self.calls = 0

        def generate(self, _schema_name, _prompt, requested_model):
            self.calls += 1
            assert requested_model is output_model
            return requested_model()

    provider = EmptyProvider()
    result = agent_factory(provider).run(DuckDBSource(bank_source_config).scan(), *args)
    assert result == output_model()
    assert provider.calls == 1


def test_orchestrator_emits_stable_stages_with_agent_metadata(bank_source_config):
    events = []
    SemanticEnricher(MockProvider()).enrich(
        DuckDBSource(bank_source_config).scan(),
        on_stage=lambda stage, status, _summary, details: events.append((stage, status, details)),
    )
    semantic_events = [event for event in events if event[0].endswith("semantics")]
    assert [(stage, status) for stage, status, _details in semantic_events] == [
        ("business_semantics", "started"),
        ("business_semantics", "completed"),
        ("relationship_semantics", "started"),
        ("relationship_semantics", "completed"),
        ("query_semantics", "started"),
        ("query_semantics", "completed"),
    ]
    assert [details["agent_id"] for _stage, _status, details in semantic_events] == [
        "semantic_inventory", "semantic_inventory", "relationship", "relationship", "metric_rule", "metric_rule"
    ]


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (ConceptCandidate, {
            "id": "account", "name": "Account", "description": "Account", "aliases": [],
            "classification": "internal", "maps_to": [], "warnings": [],
        }, "maps_to"),
        (MetricCandidate, {
            "id": "volume", "name": "Volume", "description": "Volume", "classification": "internal",
            "formula": "COUNT(*)", "dependencies": [], "filters": [], "grain": "aggregate", "warnings": [],
        }, "dependencies"),
        (PolicyCandidate, {
            "id": "handling", "name": "Handling", "description": "Handling", "classification": "restricted",
            "applies_to": [], "rule": "Aggregate only", "confidence": 0.8,
            "evidence": ["customers.email"], "warnings": [],
        }, "applies_to"),
    ],
)
def test_emitted_semantics_require_targets(model, payload, field):
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    assert field in str(exc.value)


def test_whole_semantic_categories_may_be_empty():
    business = BusinessSemantics(table_purposes={}, concepts=[], policies=[], classifications={})
    query = QuerySemantics(grains={}, dimensions=[], measures=[], joins=[], guidance=[], warnings=[])
    assert business.concepts == business.entities == business.dimensions == business.policies == []
    assert query.measures == query.structured_measures == query.rules == []


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (BusinessSemantics, {"unexpected": []}, "unexpected"),
        (QuerySemantics, {"metrics": []}, "metrics"),
        (RelationshipSemantics, {"joins": []}, "joins"),
    ],
)
def test_agent_output_envelopes_reject_unknown_fields(model, payload, field):
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    assert field in str(exc.value)


def test_relationship_candidates_reject_unknown_fields():
    with pytest.raises(ValidationError, match="unexpected"):
        RelationshipSemantics.model_validate({
            "relationships": [{
                "id": "account-customer",
                "source_table": "accounts",
                "source_column": "customer_id",
                "target_table": "customers",
                "target_column": "customer_id",
                "cardinality": "many-to-one",
                "unexpected": "ignored today",
            }]
        })


def test_fallback_is_credential_free():
    proposal = SemanticEnricher().fallback()
    assert proposal.generation_mode == "fallback"
    assert proposal.provider == "deterministic-structural-fallback"


class FailingStageProvider(MockProvider):
    """Behaves like MockProvider but raises on one named schema, simulating an
    exhausted-retry provider failure (e.g. a persistent timeout) for that stage only."""

    def __init__(self, failing_schema):
        super().__init__()
        self.failing_schema = failing_schema

    def generate(self, schema_name, prompt, output_model):
        if schema_name == self.failing_schema:
            self.calls.append((schema_name, prompt, output_model))
            raise RuntimeError(f"Provider failed to return valid {schema_name}: Request timed out.")
        return super().generate(schema_name, prompt, output_model)


def test_enrich_degrades_failing_stage_instead_of_aborting_the_whole_run(bank_source_config):
    provider = FailingStageProvider("query_semantics")
    snapshot = DuckDBSource(bank_source_config).scan()

    proposal = SemanticEnricher(provider).enrich(snapshot)

    assert proposal.generation_mode == "partial"
    # Earlier stages still produced their live results; only the failing stage was replaced.
    assert len(proposal.business.entities) == 1
    assert len(proposal.business.policies) == 1
    assert proposal.query == QuerySemantics()


def test_enrich_degraded_stage_emits_degraded_stage_event(bank_source_config):
    provider = FailingStageProvider("business_semantics")
    snapshot = DuckDBSource(bank_source_config).scan()
    events = []

    proposal = SemanticEnricher(provider).enrich(
        snapshot,
        on_stage=lambda stage, status, _summary, details: events.append((stage, status, details)),
    )

    assert proposal.generation_mode == "partial"
    assert proposal.business == BusinessSemantics()
    degraded_events = [event for event in events if event[1] == "degraded"]
    assert len(degraded_events) == 1
    assert degraded_events[0][0] == "business_semantics"
    assert degraded_events[0][2]["agent_id"] == "semantic_inventory"
    # Downstream stages still ran to completion.
    assert ("relationship_semantics", "completed", {"agent_id": "relationship", "proposed_relationships": 0}) in [
        (stage, status, details) for stage, status, details in events
    ]


def test_fallback_events_keep_stage_ids_and_agent_metadata():
    events = []
    SemanticEnricher().enrich(
        # The fallback does not inspect the snapshot.
        None,  # type: ignore[arg-type]
        on_stage=lambda stage, status, _summary, details: events.append((stage, status, details)),
    )
    assert [(stage, status, details["agent_id"]) for stage, status, details in events] == [
        ("business_semantics", "skipped", "semantic_inventory"),
        ("relationship_semantics", "skipped", "relationship"),
        ("query_semantics", "skipped", "metric_rule"),
    ]
