from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from .models import BusinessSemantics, CatalogSnapshot, QuerySemantics, RelationshipSemantics, SemanticProposal
from .llm import OpenAICompatibleGateway, gateway_from_environment
from .semantic.agents import GenerationProvider, MetricRuleAgent, RelationshipAgent, SemanticInventoryAgent
from .settings import Settings

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)

StageCallback = Callable[[str, str, str, dict[str, Any]], None]
TraceCallback = Callable[[str, str, dict[str, Any] | None, dict[str, Any] | None], None]


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
            llm_max_retries=configured.llm_max_retries,
            llm_retry_backoff_seconds=configured.llm_retry_backoff_seconds,
            llm_timeout_backoff_multiplier=configured.llm_timeout_backoff_multiplier,
        )
        super().__init__(settings)


class SemanticEnricher:
    def __init__(self, provider: GenerationProvider | None = None):
        self.provider = provider

    @staticmethod
    def _sanitized_snapshot(snapshot: CatalogSnapshot) -> CatalogSnapshot:
        return snapshot.model_copy(update={"database_path": "[omitted]"}, deep=True)

    def enrich(
        self,
        snapshot: CatalogSnapshot,
        on_stage: StageCallback | None = None,
        on_trace: TraceCallback | None = None,
        include_query_semantics: bool = True,
    ) -> SemanticProposal:
        if self.provider is None:
            for stage, label, agent_id in (
                ("business_semantics", "semantic inventory", SemanticInventoryAgent.agent_id),
                ("relationship_semantics", "relationship semantics", RelationshipAgent.agent_id),
                ("query_semantics", "metrics and rules", MetricRuleAgent.agent_id),
            ):
                if stage == "query_semantics" and not include_query_semantics:
                    if on_trace:
                        on_trace(
                            stage,
                            "skipped",
                            {"reason": "post_activation_authoring"},
                            {"structured_measures": [], "rules": []},
                        )
                    if on_stage:
                        on_stage(
                            stage,
                            "skipped",
                            "Skipped metrics and business rules; they are authored after graph activation.",
                            {"agent_id": agent_id, "reason": "post_activation_authoring"},
                        )
                    continue
                if on_trace:
                    on_trace(stage, "skipped", {"reason": "No model provider configured."}, None)
                if on_stage:
                    on_stage(
                        stage,
                        "skipped",
                        f"Skipped {label}; using the structural fallback.",
                        {"agent_id": agent_id},
                    )
            return self.fallback()
        safe_snapshot = self._sanitized_snapshot(snapshot)
        inventory_agent = SemanticInventoryAgent(self.provider)
        relationship_agent = RelationshipAgent(self.provider)
        metric_rule_agent = MetricRuleAgent(self.provider)
        degraded = False

        if on_trace:
            on_trace("business_semantics", "started", inventory_agent.input_payload(safe_snapshot), None)
        if on_stage:
            on_stage(
                "business_semantics",
                "started",
                "Building the semantic inventory from catalog metadata.",
                {"agent_id": inventory_agent.agent_id},
            )
        business, business_degraded = self._run_stage(
            "business_semantics",
            "Semantic inventory generation",
            inventory_agent.agent_id,
            lambda: inventory_agent.run(safe_snapshot),
            BusinessSemantics,
            on_stage,
            on_trace,
        )
        degraded = degraded or business_degraded
        if not business_degraded:
            if on_trace:
                on_trace("business_semantics", "completed", None, business.model_dump(mode="json"))
            if on_stage:
                on_stage(
                    "business_semantics",
                    "completed",
                    f"Proposed {len(business.entities)} entities, {len(business.dimensions)} dimensions, and {len(business.policies)} policies.",
                    {
                        "agent_id": inventory_agent.agent_id,
                        "entities": len(business.entities),
                        "dimensions": len(business.dimensions),
                        "legacy_concepts": len(business.concepts),
                        "policies": len(business.policies),
                        "policy_targets": sum(len(policy.applies_to) for policy in business.policies),
                        "classified_tables": len(business.classifications),
                    },
                )

        if on_trace:
            on_trace(
                "relationship_semantics",
                "started",
                relationship_agent.input_payload(safe_snapshot, business),
                None,
            )
        if on_stage:
            on_stage(
                "relationship_semantics",
                "started",
                "Checking and proposing semantic relationships.",
                {"agent_id": relationship_agent.agent_id},
            )
        relationships, relationship_degraded = self._run_stage(
            "relationship_semantics",
            "Relationship semantics generation",
            relationship_agent.agent_id,
            lambda: relationship_agent.run(safe_snapshot, business),
            RelationshipSemantics,
            on_stage,
            on_trace,
        )
        degraded = degraded or relationship_degraded
        if not relationship_degraded:
            if on_trace:
                on_trace(
                    "relationship_semantics",
                    "completed",
                    None,
                    relationships.model_dump(mode="json"),
                )
            if on_stage:
                on_stage(
                    "relationship_semantics",
                    "completed",
                    f"Proposed {len(relationships.relationships)} catalog-bounded relationships.",
                    {
                        "agent_id": relationship_agent.agent_id,
                        "proposed_relationships": len(relationships.relationships),
                    },
                )

        if include_query_semantics:
            if on_trace:
                on_trace(
                    "query_semantics",
                    "started",
                    metric_rule_agent.input_payload(safe_snapshot, business, relationships),
                    None,
                )
            if on_stage:
                on_stage(
                    "query_semantics",
                    "started",
                    "Defining metrics, business rules, dimensions, and warnings.",
                    {"agent_id": metric_rule_agent.agent_id},
                )
            query, query_degraded = self._run_stage(
                "query_semantics",
                "Metrics and rules generation",
                metric_rule_agent.agent_id,
                lambda: metric_rule_agent.run(safe_snapshot, business, relationships),
                QuerySemantics,
                on_stage,
                on_trace,
            )
            degraded = degraded or query_degraded
            if not query_degraded:
                if on_trace:
                    on_trace("query_semantics", "completed", None, query.model_dump(mode="json"))
                if on_stage:
                    on_stage(
                        "query_semantics",
                        "completed",
                        f"Proposed {len(query.structured_measures)} metrics and {len(query.rules)} business rules.",
                        {
                            "agent_id": metric_rule_agent.agent_id,
                            "metrics": len(query.structured_measures),
                            "business_rules": len(query.rules),
                            "legacy_measures": len(query.measures),
                            "metric_dependencies": sum(len(measure.dependencies) for measure in query.structured_measures),
                            "warnings": len(query.warnings),
                        },
                    )
        else:
            query = QuerySemantics()
            if on_trace:
                on_trace(
                    "query_semantics",
                    "skipped",
                    {"reason": "post_activation_authoring"},
                    {"structured_measures": [], "rules": []},
                )
            if on_stage:
                on_stage(
                    "query_semantics",
                    "skipped",
                    "Skipped metrics and business rules; they are authored after graph activation.",
                    {"agent_id": metric_rule_agent.agent_id, "reason": "post_activation_authoring"},
                )

        return SemanticProposal(
            business=business,
            relationships=relationships,
            query=query,
            generation_mode="partial" if degraded else "live",
            provider=self.provider.name,
            model=self.provider.model,
        )

    @staticmethod
    def _run_stage(
        stage: str,
        label: str,
        agent_id: str,
        run: Callable[[], OutputT],
        empty: Callable[[], OutputT],
        on_stage: StageCallback | None,
        on_trace: TraceCallback | None,
    ) -> tuple[OutputT, bool]:
        """Run one enrichment agent, degrading to an empty typed result on exhausted-retry
        failures (timeouts, malformed output) instead of aborting the whole generation run."""
        try:
            return run(), False
        except RuntimeError as exc:
            logger.warning("Enrichment stage %s degraded to structural fallback: %s", stage, exc)
            summary = f"{label} failed after exhausting retries; using the structural fallback for this stage."
            reason = str(getattr(exc, "cerebro_reason", "provider_failure"))
            fallback = empty()
            if on_stage:
                on_stage(stage, "degraded", summary, {"agent_id": agent_id, "reason": reason})
            if on_trace:
                on_trace(
                    stage,
                    "degraded",
                    None,
                    {"reason": reason, "fallback_output": fallback.model_dump(mode="json")},
                )
            return fallback, True

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


__all__ = [
    "GenerationProvider",
    "OpenAIResponsesProvider",
    "SemanticEnricher",
    "provider_from_environment",
]
