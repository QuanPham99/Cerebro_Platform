from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from typing import Callable

from .models import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    GroundingResponse,
    RankedResult,
    SemanticBundle,
    SemanticObject,
)

TOKEN = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "of", "on", "or", "the", "there", "to", "what", "who", "with",
}


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOP_WORDS]


class SemanticRetriever:
    def __init__(
        self,
        bundle: SemanticBundle,
        embedder: Callable[[list[str]], list[list[float]]] | None = None,
    ):
        self.bundle = bundle
        self.by_id = bundle.by_id()
        self.embedder = embedder
        self.documents = {obj.id: self._search_text(obj) for obj in bundle.objects}
        self.adjacency: dict[str, set[str]] = defaultdict(set)
        self.edges = self._build_edges()
        self._vectors: dict[str, list[float]] = {}
        if embedder:
            try:
                vectors = embedder([self.documents[obj.id] for obj in bundle.objects])
                self._vectors = {obj.id: vector for obj, vector in zip(bundle.objects, vectors)}
            except Exception:
                self.embedder = None
                self._vectors = {}

    @staticmethod
    def _search_text(obj: SemanticObject) -> str:
        columns = " ".join(str(col.get("name", "")) for col in obj.cerebro.get("columns", []))
        return " ".join([obj.id, obj.name, obj.description, *obj.aliases, *obj.tags, columns, obj.body])

    def _add_edge(self, edge: GraphEdge) -> None:
        if edge.source in self.by_id and edge.target in self.by_id:
            self.adjacency[edge.source].add(edge.target)
            self.adjacency[edge.target].add(edge.source)

    def _build_edges(self) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for obj in self.bundle.objects:
            if obj.type == "relationship":
                source = str(obj.cerebro.get("source_table", ""))
                target = str(obj.cerebro.get("target_table", ""))
                edge = GraphEdge(id=f"edge.physical.{obj.id}", source=source, target=target, type="physical_fk", label=str(obj.cerebro.get("cardinality", "")))
                edges.append(edge)
                self._add_edge(edge)
                for endpoint in (source, target):
                    membership = GraphEdge(id=f"{obj.id}:{endpoint}", source=obj.id, target=endpoint, type="relationship_endpoint", label="endpoint")
                    edges.append(membership)
                    self._add_edge(membership)
                continue

            if obj.type == "dataset":
                targets = list(obj.links)
                edge_type = "semantic_mapping"
                label = "contains"
            elif obj.type == "concept":
                targets = [str(value) for value in obj.cerebro.get("maps_to", obj.links)]
                edge_type = "semantic_mapping"
                label = "maps to"
            elif obj.type == "metric":
                targets = [str(value) for value in obj.cerebro.get("dependencies", obj.links)]
                edge_type = "metric_dependency"
                label = "depends on"
            elif obj.type == "policy":
                targets = [str(value) for value in obj.cerebro.get("applies_to", obj.links)]
                edge_type = "policy_coverage"
                label = "applies to"
            else:
                continue

            for target in targets:
                if target not in self.by_id:
                    continue
                key = (obj.id, target, edge_type)
                if key in seen:
                    continue
                seen.add(key)
                edge = GraphEdge(id=f"{obj.id}->{target}", source=obj.id, target=target, type=edge_type, label=label)
                edges.append(edge)
                self._add_edge(edge)
        return edges

    def lexical_rank(self, query: str) -> list[tuple[str, float, list[str]]]:
        terms = _tokens(query)
        ranked = []
        for obj in self.bundle.objects:
            haystack = _tokens(self.documents[obj.id])
            counts = {term: haystack.count(term) for term in set(terms)}
            matched = [term for term in terms if counts.get(term, 0)]
            if not matched:
                continue
            score = sum(1.0 + math.log1p(counts[term]) for term in set(matched))
            if any(term in _tokens(obj.name) for term in terms):
                score += 2.0
            ranked.append((obj.id, score, [f"lexical:{term}" for term in sorted(set(matched))]))
        return sorted(ranked, key=lambda item: (-item[1], item[0]))

    def _vector_rank(self, query: str) -> list[tuple[str, float]]:
        if not self.embedder or not self._vectors:
            return []
        query_vector = self.embedder([query])[0]
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        scored = []
        for object_id, vector in self._vectors.items():
            denominator = query_norm * math.sqrt(sum(value * value for value in vector))
            score = sum(a * b for a, b in zip(query_vector, vector)) / denominator if denominator else 0.0
            scored.append((object_id, score))
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    def search(self, query: str, limit: int = 10, types: set[str] | None = None) -> list[RankedResult]:
        if not query.strip():
            return []
        lexical = self.lexical_rank(query)
        vector = self._vector_rank(query)
        fused: dict[str, float] = defaultdict(float)
        evidence: dict[str, list[str]] = defaultdict(list)
        for rank, (object_id, _, reasons) in enumerate(lexical, 1):
            fused[object_id] += 1 / (60 + rank)
            evidence[object_id].extend(reasons)
        for rank, (object_id, score) in enumerate(vector, 1):
            fused[object_id] += 1 / (60 + rank)
            evidence[object_id].append(f"vector:{score:.3f}")
        seed_scores = dict(fused)
        for object_id, seed_score in seed_scores.items():
            for neighbor in self.adjacency.get(object_id, set()):
                fused[neighbor] += seed_score * 0.05
                evidence[neighbor].append(f"one-hop:{object_id}")
        allowed = [
            (object_id, score)
            for object_id, score in fused.items()
            if object_id in self.by_id
            and self.by_id[object_id].status == "active"
            and (not types or self.by_id[object_id].type in types)
        ]
        allowed.sort(key=lambda item: (-item[1], item[0]))
        return [
            RankedResult(
                id=object_id,
                type=self.by_id[object_id].type,
                name=self.by_id[object_id].name,
                score=round(score, 8),
                evidence=evidence[object_id],
            )
            for object_id, score in allowed[:limit]
        ]

    def expand(self, concept_ids: list[str], depth: int = 1) -> list[str]:
        visited = {object_id for object_id in concept_ids if object_id in self.by_id}
        frontier = set(visited)
        for _ in range(max(0, depth)):
            frontier = {neighbor for item in frontier for neighbor in self.adjacency.get(item, set())} - visited
            visited.update(frontier)
        return sorted(visited)

    def graph(self) -> GraphResponse:
        nodes = [
            GraphNode(
                id=obj.id,
                type=obj.type,
                label=obj.name,
                description=obj.description,
                classification=str(obj.cerebro.get("classification", "internal")),
            )
            for obj in self.bundle.objects
        ]
        return GraphResponse(version=self.bundle.version, nodes=nodes, edges=self.edges)

    def grounding(self, question: str, limit: int = 10) -> GroundingResponse:
        ranked = self.search(question, limit=limit)
        selected_ids = self.expand([item.id for item in ranked], depth=1)
        selected = [self.by_id[item] for item in selected_ids]
        tables = [obj for obj in selected if obj.type == "table"]
        relationships = [obj for obj in selected if obj.type == "relationship"]
        metrics = [obj for obj in selected if obj.type == "metric"]
        concepts = [obj for obj in selected if obj.type == "concept"]
        columns = sorted({f"{obj.id}.{col.get('name')}" for obj in tables for col in obj.cerebro.get("columns", [])})
        warnings = sorted({str(warning) for obj in selected for warning in obj.cerebro.get("warnings", [])})
        classifications = sorted({str(obj.cerebro.get("classification", "internal")) for obj in selected})
        return GroundingResponse(
            semantic_version=self.bundle.version,
            retrieval_mode="hybrid_graph" if self.embedder else "lexical_graph",
            question=question,
            concepts=[self._summary(obj) for obj in concepts],
            tables=[self._summary(obj) for obj in tables],
            columns=columns,
            joins=[{"id": obj.id, **obj.cerebro} for obj in relationships],
            grain=[str(obj.cerebro.get("grain")) for obj in tables if obj.cerebro.get("grain")],
            metrics=[{"id": obj.id, **obj.cerebro} for obj in metrics],
            filters=[item for obj in metrics for item in obj.cerebro.get("filters", [])],
            warnings=warnings,
            classifications=classifications,
            provenance=[{"id": obj.id, **obj.provenance} for obj in selected],
            ranking_evidence=ranked,
        )

    @staticmethod
    def _summary(obj: SemanticObject) -> dict[str, object]:
        return {"id": obj.id, "name": obj.name, "description": obj.description, "cerebro": obj.cerebro}


class OpenAIEmbedder:
    """Optional in-memory embedding adapter; lexical retrieval remains the safe fallback."""

    def __init__(self, model: str = "text-embedding-3-small", *, api_key: str | None = None, base_url: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def __call__(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


def embedder_from_environment() -> OpenAIEmbedder | None:
    from .settings import Settings

    settings = Settings.from_environment()
    if not settings.llm_api_key or not settings.embedding_model:
        return None
    try:
        return OpenAIEmbedder(
            settings.embedding_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    except (ImportError, RuntimeError):
        return None
