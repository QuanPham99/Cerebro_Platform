from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import BusinessSemantics, CatalogSnapshot, QuerySemantics, SemanticProposal
from .paths import DEFAULT_BUNDLE

OutputT = TypeVar("OutputT", bound=BaseModel)


class GenerationProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        """Return validated structured output without receiving source rows."""


class OpenAIResponsesProvider(GenerationProvider):
    name = "openai"

    def __init__(self, model: str = "gpt-5.4-mini"):
        self.model = model
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the 'ai' extra to enable live generation") from exc
        self.client = OpenAI()

    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": "Return only the requested semantic metadata. Never infer or request source rows."},
                {"role": "user", "content": prompt},
            ],
            text_format=output_model,
        )
        if response.output_parsed is None:
            raise RuntimeError(f"Provider returned no structured {schema_name} output")
        return response.output_parsed


class SemanticEnricher:
    def __init__(self, provider: GenerationProvider | None = None):
        self.provider = provider

    @staticmethod
    def _metadata(snapshot: CatalogSnapshot) -> str:
        safe = snapshot.model_dump(mode="json")
        safe["database_path"] = "[local source path omitted]"
        return json.dumps(safe, indent=2, sort_keys=True)

    def enrich(self, snapshot: CatalogSnapshot) -> SemanticProposal:
        if self.provider is None:
            return self.fallback()
        metadata = self._metadata(snapshot)
        business = self.provider.generate(
            "business_semantics",
            "Stage 1 — define business purposes, concepts, aliases, and classifications "
            "from this catalog-only snapshot:\n" + metadata,
            BusinessSemantics,
        )
        query = self.provider.generate(
            "query_semantics",
            "Stage 2 — define grain, dimensions, measures, declared joins, time rules, and "
            "fan-out warnings from this catalog-only snapshot:\n" + metadata,
            QuerySemantics,
        )
        return SemanticProposal(
            business=business,
            query=query,
            generation_mode="live",
            provider=self.provider.name,
            model=self.provider.model,
        )

    @staticmethod
    def fallback(bundle_path: Path | str = DEFAULT_BUNDLE) -> SemanticProposal:
        path = Path(bundle_path)
        if not path.exists():
            raise FileNotFoundError(f"Golden fallback bundle not found: {path}")
        return SemanticProposal(
            business=BusinessSemantics(table_purposes={}, concepts=[], classifications={}),
            query=QuerySemantics(grains={}, dimensions=[], measures=[], joins=[], guidance=[], warnings=[]),
            generation_mode="fallback",
            provider="checked-in-golden-bundle",
            model="none",
        )


def provider_from_environment() -> GenerationProvider | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    return OpenAIResponsesProvider(os.getenv("CEREBRO_OPENAI_MODEL", "gpt-5.4-mini"))

