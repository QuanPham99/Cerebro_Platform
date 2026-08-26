from cerebro.enrichment import GenerationProvider, SemanticEnricher
from cerebro.models import BusinessSemantics, QuerySemantics
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
                concepts=[{"id": "account"}],
                classifications={"accounts.balance": "confidential"},
            )
        return QuerySemantics(
            grains={"accounts": "one account"},
            dimensions=[],
            measures=[],
            joins=[],
            guidance=["Use declared joins"],
            warnings=[],
        )


def test_two_structured_enrichment_stages_are_catalog_only():
    provider = MockProvider()
    proposal = SemanticEnricher(provider).enrich(DuckDBSource().scan())
    assert proposal.generation_mode == "live"
    assert [call[0] for call in provider.calls] == ["business_semantics", "query_semantics"]
    prompts = "\n".join(call[1] for call in provider.calls)
    assert "row_sampling\"" in prompts
    assert '"disabled"' in prompts
    assert "Pooja Garcia" not in prompts
    assert "customer0@mailbank.com" not in prompts


def test_fallback_is_credential_free():
    proposal = SemanticEnricher().fallback()
    assert proposal.generation_mode == "fallback"
    assert proposal.provider == "checked-in-golden-bundle"

