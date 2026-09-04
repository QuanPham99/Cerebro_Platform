from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from .models import BusinessSemantics, CatalogSnapshot, QuerySemantics, RelationshipSemantics, SemanticProposal
from .llm import OpenAICompatibleGateway, gateway_from_environment
from .settings import Settings

OutputT = TypeVar("OutputT", bound=BaseModel)
StageCallback = Callable[[str, str, str, dict[str, Any]], None]


class GenerationProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        """Return validated structured output without receiving source rows."""


class OpenAIResponsesProvider(OpenAICompatibleGateway, GenerationProvider):
    """Backward-compatible name for the configurable OpenAI-compatible gateway."""

    def __init__(self, model: str = "gpt-5.4-mini"):
        configured = Settings.from_environment()
        settings = Settings(
            database_path=configured.database_path,
            database_schema=configured.database_schema,
            llm_base_url=configured.llm_base_url,
            llm_api_key=configured.llm_api_key,
            llm_model=configured.llm_model or model,
            llm_response_mode=configured.llm_response_mode,
            embedding_model=configured.embedding_model,
            query_row_limit=configured.query_row_limit,
            query_timeout_seconds=configured.query_timeout_seconds,
            llm_provider_id=configured.llm_provider_id,
            llm_provider_name=configured.llm_provider_name,
            llm_timeout_seconds=configured.llm_timeout_seconds,
            llm_max_output_tokens=configured.llm_max_output_tokens,
        )
        super().__init__(settings)


class SemanticEnricher:
    def __init__(self, provider: GenerationProvider | None = None):
        self.provider = provider

    @staticmethod
    def _metadata(snapshot: CatalogSnapshot) -> str:
        safe = snapshot.model_dump(mode="json")
        safe.pop("database_path", None)
        return json.dumps(safe, indent=2, sort_keys=True)

    def enrich(self, snapshot: CatalogSnapshot, on_stage: StageCallback | None = None) -> SemanticProposal:
        if self.provider is None:
            for stage, label in (
                ("business_semantics", "business semantics"),
                ("relationship_semantics", "relationship semantics"),
                ("query_semantics", "query semantics"),
            ):
                if on_stage:
                    on_stage(stage, "skipped", f"Skipped {label}; using the structural fallback.", {})
            return self.fallback()
        metadata = self._metadata(snapshot)
        if on_stage:
            on_stage("business_semantics", "started", "Defining business meaning from catalog metadata.", {})
        business = self.provider.generate(
            "business_semantics",
            "Stage 1 — define business purposes, typed concepts, and reviewable policies from this "
            "catalog-only snapshot. Every concept must map_to one or more real table names. Policies "
            "may be proposed only when table or column names clearly support them; every policy must "
            "apply_to real table names and include a rule, confidence, and evidence containing exact "
            "catalog identifiers such as table.column. Return an empty category rather than inventing "
            "unsupported semantics. All proposed semantics remain ai_proposed:\n" + metadata,
            BusinessSemantics,
        )
        if on_stage:
            on_stage(
                "business_semantics",
                "completed",
                f"Proposed {len(business.concepts)} business concepts and {len(business.policies)} policies.",
                {
                    "concepts": len(business.concepts),
                    "concept_mappings": sum(len(concept.maps_to) for concept in business.concepts),
                    "policies": len(business.policies),
                    "policy_targets": sum(len(policy.applies_to) for policy in business.policies),
                    "classified_tables": len(business.classifications),
                },
            )
            on_stage("relationship_semantics", "started", "Checking and proposing semantic relationships.", {})
        relationships = self.provider.generate(
            "relationship_semantics",
            "Stage 2 — propose relationships using only real table and column names in this "
            "catalog snapshot. Return source_table, source_column, target_table, target_column, "
            "cardinality, confidence, and evidence. Never invent endpoints:\n" + metadata,
            RelationshipSemantics,
        )
        if on_stage:
            on_stage(
                "relationship_semantics",
                "completed",
                f"Proposed {len(relationships.relationships)} catalog-bounded relationships.",
                {"proposed_relationships": len(relationships.relationships)},
            )
            on_stage("query_semantics", "started", "Defining grains, dimensions, measures, and warnings.", {})
        query = self.provider.generate(
            "query_semantics",
            "Stage 3 — define grain, dimensions, typed measures, time rules, and fan-out warnings "
            "from this catalog-only snapshot. Every emitted measure must include a formula and one "
            "or more dependencies using real table names. Return no measure rather than inventing an "
            "unsupported formula or dependency:\n" + metadata,
            QuerySemantics,
        )
        if on_stage:
            on_stage(
                "query_semantics",
                "completed",
                f"Proposed {len(query.measures)} measures and {len(query.dimensions)} dimensions.",
                {
                    "dimensions": len(query.dimensions),
                    "measures": len(query.measures),
                    "metric_dependencies": sum(len(measure.dependencies) for measure in query.measures),
                    "warnings": len(query.warnings),
                },
            )
        return SemanticProposal(
            business=business,
            relationships=relationships,
            query=query,
            generation_mode="live",
            provider=self.provider.name,
            model=self.provider.model,
        )

    @staticmethod
    def fallback(bundle_path: Path | str | None = None) -> SemanticProposal:
        return SemanticProposal(
            business=BusinessSemantics(table_purposes={}, concepts=[], policies=[], classifications={}),
            relationships=RelationshipSemantics(),
            query=QuerySemantics(grains={}, dimensions=[], measures=[], joins=[], guidance=[], warnings=[]),
            generation_mode="fallback",
            provider="deterministic-structural-fallback",
            model="none",
        )


def provider_from_environment() -> GenerationProvider | None:
    return gateway_from_environment()  # type: ignore[return-value]
