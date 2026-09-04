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
                concepts=[{
                    "id": "account",
                    "name": "Account",
                    "description": "A customer-held banking account.",
                    "aliases": [],
                    "classification": "confidential",
                    "maps_to": ["accounts"],
                    "warnings": [],
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
            measures=[{
                "id": "transaction-volume",
                "name": "Transaction volume",
                "description": "Total transaction amount.",
                "classification": "confidential",
                "formula": "SUM(transactions.amount)",
                "dependencies": ["transactions"],
                "filters": [],
                "grain": "aggregate over transactions",
                "warnings": [],
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
    assert len(proposal.query.measures) == 1


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
    assert business.concepts == business.policies == []
    assert query.measures == []


def test_fallback_is_credential_free():
    proposal = SemanticEnricher().fallback()
    assert proposal.generation_mode == "fallback"
    assert proposal.provider == "deterministic-structural-fallback"
