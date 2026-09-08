from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from ..models import BusinessSemantics, CatalogSnapshot, QuerySemantics, RelationshipSemantics

OutputT = TypeVar("OutputT", bound=BaseModel)


class GenerationProvider(ABC):
    """Provider-neutral structured generation boundary for bounded semantic agents."""

    name: str
    model: str

    @abstractmethod
    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        """Return validated structured output without receiving source rows."""


def _catalog_payload(snapshot: CatalogSnapshot) -> dict:
    payload = snapshot.model_dump(mode="json", exclude={"database_path"})
    return payload


def _render(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


class SemanticInventoryAgent:
    agent_id = "semantic_inventory"
    schema_name = "business_semantics"

    def __init__(self, provider: GenerationProvider):
        self.provider = provider

    @staticmethod
    def input_payload(snapshot: CatalogSnapshot) -> dict:
        return {"catalog": _catalog_payload(snapshot)}

    def run(self, snapshot: CatalogSnapshot) -> BusinessSemantics:
        payload = self.input_payload(snapshot)
        prompt = (
            "SemanticInventoryAgent — propose typed entities and dimensions, business purposes, "
            "and reviewable policies from this catalog-only snapshot. Every entity physical mapping "
            "and dimension binding must use real table and column names. Policies may be proposed "
            "only from catalog evidence. Set compatible_metrics to an empty list on every dimension; "
            "the later MetricRuleAgent owns metric compatibility and the linker derives reverse links. "
            "Policies may be proposed only when table or column names clearly support them; every "
            "policy must apply_to real table names and include a rule, confidence, and evidence containing exact catalog "
            "identifiers such as table.column. Return an empty typed category rather than inventing "
            "unsupported semantics. Leave legacy concepts empty. All proposals remain ai_proposed. "
            "Work directly from the catalog below without restating it or narrating your reasoning; "
            "large catalogs must still complete within the request timeout, so keep every field concise. "
            "Do not request or infer source rows:\n"
            + _render(payload["catalog"])
        )
        return self.provider.generate(self.schema_name, prompt, BusinessSemantics)


class RelationshipAgent:
    agent_id = "relationship"
    schema_name = "relationship_semantics"

    def __init__(self, provider: GenerationProvider):
        self.provider = provider

    @staticmethod
    def input_payload(snapshot: CatalogSnapshot, inventory: BusinessSemantics) -> dict:
        return {
            "catalog": _catalog_payload(snapshot),
            "semantic_inventory": {
                "entities": [item.model_dump(mode="json") for item in inventory.entities],
                "dimensions": [item.model_dump(mode="json") for item in inventory.dimensions],
            },
        }

    def run(self, snapshot: CatalogSnapshot, inventory: BusinessSemantics) -> RelationshipSemantics:
        payload = self.input_payload(snapshot, inventory)
        prompt = (
            "RelationshipAgent — propose relationships using only real table and column names in the "
            "catalog snapshot. Return source_table, source_column, target_table, target_column, optional "
            "source_entity and target_entity identifiers from the supplied semantic inventory, cardinality, "
            "confidence, and catalog evidence. Never invent endpoints and return an empty typed collection "
            "when the catalog cannot support a relationship. Work directly from the input without restating "
            "it or narrating your reasoning; large catalogs must still complete within the request timeout, "
            "so keep every field concise. Do not read or infer source rows:\n"
            + _render(payload)
        )
        return self.provider.generate(self.schema_name, prompt, RelationshipSemantics)


class MetricRuleAgent:
    agent_id = "metric_rule"
    schema_name = "query_semantics"

    def __init__(self, provider: GenerationProvider):
        self.provider = provider

    @staticmethod
    def input_payload(
        snapshot: CatalogSnapshot,
        inventory: BusinessSemantics,
        relationships: RelationshipSemantics,
    ) -> dict:
        return {
            "catalog": _catalog_payload(snapshot),
            "semantic_inventory": {
                "entities": [item.model_dump(mode="json") for item in inventory.entities],
                "dimensions": [item.model_dump(mode="json") for item in inventory.dimensions],
            },
            "relationships": [item.model_dump(mode="json") for item in relationships.relationships],
        }

    def run(
        self,
        snapshot: CatalogSnapshot,
        inventory: BusinessSemantics,
        relationships: RelationshipSemantics,
    ) -> QuerySemantics:
        payload = self.input_payload(snapshot, inventory, relationships)
        prompt = (
            "MetricRuleAgent — propose structured aggregate or ratio metrics and typed business rules "
            "from this catalog-only snapshot and the identifiers proposed by the prior agents. Every metric "
            "must bind to a proposed entity, real table dependencies, real columns, and proposed compatible "
            "dimensions. Every rule must bind to a proposed entity and existing semantic dependencies. Leave "
            "legacy measures empty. Return an empty typed category rather than inventing unsupported semantics. "
            "Work directly from the input without restating it or narrating your reasoning; large catalogs "
            "must still complete within the request timeout, so keep every field concise. "
            "Do not read or infer source rows:\n"
            + _render(payload)
        )
        return self.provider.generate(self.schema_name, prompt, QuerySemantics)


__all__ = [
    "GenerationProvider",
    "MetricRuleAgent",
    "RelationshipAgent",
    "SemanticInventoryAgent",
]
