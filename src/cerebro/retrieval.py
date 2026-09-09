from __future__ import annotations

import hashlib
import math
import os
import re
from collections import defaultdict
from typing import Callable

from .models import (
    EXPRESSION_TYPE_REGISTRY_VERSION,
    GROUNDING_SNAPSHOT_VERSION,
    LITERAL_SPAN_REGISTRY_VERSION,
    QUESTION_CANONICALIZATION_VERSION,
    AuthorizationScope,
    ColumnRef,
    DialectCapabilities,
    GraphEdge,
    GraphNode,
    GraphResponse,
    GroundingResponse,
    GroundingSnapshot,
    RankedResult,
    SemanticBundle,
    SemanticObject,
    SnapshotColumn,
    SnapshotGovernedLiteral,
    SnapshotMetadataObject,
    SnapshotRankingEvidence,
    SnapshotRelationship,
    SnapshotWarning,
)
from .provenance import (
    authorization_scope_sha256,
    canonicalize_question,
    grounding_snapshot_sha256,
)
from .semantic.linker import profile_edges
from .semantic.profile import normalize_profile_kind

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
            if obj.profile_kind == "relationship":
                physical = obj.cerebro.get("physical", {})
                physical_source = physical.get("source", {}) if isinstance(physical, dict) else {}
                physical_target = physical.get("target", {}) if isinstance(physical, dict) else {}
                source = str(obj.cerebro.get("source_table") or physical_source.get("table") or "")
                target = str(obj.cerebro.get("target_table") or physical_target.get("table") or "")
                cardinality_value = obj.cerebro.get("cardinality", "")
                label = (
                    f"{cardinality_value.get('source')}-to-{cardinality_value.get('target')}"
                    if isinstance(cardinality_value, dict)
                    else str(cardinality_value)
                )
                edge = GraphEdge(id=f"edge.physical.{obj.id}", source=source, target=target, type="physical_fk", label=label)
                edges.append(edge)
                self._add_edge(edge)
                for endpoint in (source, target):
                    membership = GraphEdge(id=f"{obj.id}:{endpoint}", source=obj.id, target=endpoint, type="relationship_endpoint", label="endpoint")
                    edges.append(membership)
                    self._add_edge(membership)
                semantic = obj.cerebro.get("semantic", {})
                if isinstance(semantic, dict):
                    semantic_source = str(semantic.get("from", ""))
                    semantic_target = str(semantic.get("to", ""))
                    semantic_edge = GraphEdge(
                        id=f"edge.semantic.{obj.id}",
                        source=semantic_source,
                        target=semantic_target,
                        type="semantic_relationship",
                        label=label,
                    )
                    if semantic_source in self.by_id and semantic_target in self.by_id:
                        edges.append(semantic_edge)
                        self._add_edge(semantic_edge)
                continue

            for target, edge_type, label in profile_edges(obj):
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
        normalized_query = " ".join(terms)
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
            names = [obj.name, *obj.aliases]
            if any(" ".join(_tokens(name)) == normalized_query for name in names):
                score += 4.0
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
        query_terms = set(_tokens(query))
        explicit_kind = {
            "entity": "entity" in query_terms,
            "dimension": "dimension" in query_terms,
            "metric": "metric" in query_terms,
            "business_rule": "rule" in query_terms,
            "relationship": "relationship" in query_terms or "join" in query_terms,
            "policy": "policy" in query_terms,
        }
        metric_intent = bool(query_terms & {"average", "avg", "balance", "count", "percentage", "rate", "total", "volume"})
        for object_id in list(fused):
            kind = self.by_id[object_id].profile_kind
            factor = {
                "dataset": 0.2,
                "physical_table": 0.7,
                "entity": 1.1,
                "dimension": 1.2,
                "metric": 1.45 if metric_intent else 1.15,
                "business_rule": 1.1,
                "relationship": 0.8,
                "policy": 0.6,
                "legacy_concept": 0.9,
            }.get(kind, 0.5)
            if explicit_kind.get(kind):
                factor *= 1.8
            fused[object_id] *= factor
        normalized_types = None
        if types:
            normalized_types = {
                "legacy_concept" if value.lower() == "concept" else
                "physical_table" if value.lower() == "table" else
                normalize_profile_kind(value)
                for value in types
            }
        allowed = [
            (object_id, score)
            for object_id, score in fused.items()
            if object_id in self.by_id
            and self.by_id[object_id].status == "stable"
            and (not normalized_types or self.by_id[object_id].profile_kind in normalized_types)
        ]
        allowed.sort(key=lambda item: (-item[1], item[0]))
        return [
            RankedResult(
                id=object_id,
                type=self.by_id[object_id].type,
                name=self.by_id[object_id].name,
                profile_kind=self.by_id[object_id].profile_kind,
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
                profile_kind=obj.profile_kind,
            )
            for obj in self.bundle.objects
        ]
        return GraphResponse(version=self.bundle.version, nodes=nodes, edges=self.edges)

    def grounding(self, question: str, limit: int = 10) -> GroundingResponse:
        ranked = self.search(question, limit=limit)
        selected_ids = self._progressive_grounding_ids(question, ranked)
        selected = [self.by_id[item] for item in selected_ids]
        tables = [obj for obj in selected if obj.profile_kind == "physical_table"]
        relationships = [obj for obj in selected if obj.profile_kind == "relationship"]
        metrics = [obj for obj in selected if obj.profile_kind == "metric"]
        concepts = [obj for obj in selected if obj.profile_kind == "legacy_concept"]
        entities = [obj for obj in selected if obj.profile_kind == "entity"]
        dimensions = [obj for obj in selected if obj.profile_kind == "dimension"]
        rules = [obj for obj in selected if obj.profile_kind == "business_rule"]
        columns = sorted({f"{obj.id}.{col.get('name')}" for obj in tables for col in obj.cerebro.get("columns", [])})
        warnings = sorted({str(warning) for obj in selected for warning in obj.cerebro.get("warnings", [])})
        classifications = sorted({str(obj.cerebro.get("classification", "internal")) for obj in selected})
        return GroundingResponse(
            semantic_version=self.bundle.version,
            retrieval_mode="hybrid_graph" if self.embedder else "lexical_graph",
            question=question,
            concepts=[self._summary(obj) for obj in concepts],
            entities=[self._summary(obj) for obj in entities],
            dimensions=[self._summary(obj) for obj in dimensions],
            rules=[self._summary(obj) for obj in rules],
            tables=[self._summary(obj) for obj in tables],
            columns=columns,
            joins=[{"id": obj.id, **obj.cerebro} for obj in relationships],
            grain=[str(obj.cerebro.get("grain")) for obj in tables if obj.cerebro.get("grain")],
            metrics=[{"id": obj.id, **obj.cerebro} for obj in metrics],
            filters=[item for obj in metrics for item in obj.cerebro.get("filters", [])],
            warnings=warnings,
            classifications=classifications,
            provenance=[{
                "id": obj.id,
                "sources": obj.sources,
                "generated": obj.generated,
                "verified": obj.verified,
                "legacy": obj.provenance,
            } for obj in selected],
            ranking_evidence=ranked,
        )

    def _progressive_grounding_ids(self, question: str, ranked: list[RankedResult]) -> list[str]:
        """Resolve semantic intent first, then only its required physical graph."""
        direct = [
            item for item in ranked
            if any(value.startswith(("lexical:", "vector:")) for value in item.evidence)
        ]
        limits = {
            "metric": 1,
            "dimension": 1,
            "business_rule": 3,
            "entity": 1,
            "physical_table": 1,
            "relationship": 1,
            "policy": 1,
            "legacy_concept": 1,
        }
        seeds: list[str] = []
        question_terms = set(_tokens(question))
        explicit_entity = "entity" in question_terms
        explicit_relationship = bool(question_terms & {"join", "relationship"})
        explicit_table = bool(question_terms & {"mapping", "physical", "schema", "table"})
        rule_intent = bool(question_terms & {"active", "anchor", "direction", "fraudulent", "rule"})
        metric_intent = bool(question_terms & {"average", "avg", "balance", "count", "many", "percentage", "rate", "total", "volume"})
        requested_kinds: set[str] = set()
        explicit_pairs = {
            "entity": "entity",
            "dimension": "dimension",
            "metric": "metric",
            "rule": "business_rule",
            "relationship": "relationship",
            "join": "relationship",
            "policy": "policy",
        }
        requested_kinds.update(kind for term, kind in explicit_pairs.items() if term in question_terms)
        if not requested_kinds:
            if metric_intent:
                requested_kinds.add("metric")
            if rule_intent and not metric_intent:
                requested_kinds.add("business_rule")
            if " by " in question.lower():
                requested_kinds.add("dimension")
        elif " by " in question.lower() and requested_kinds & {"metric", "business_rule"}:
            requested_kinds.add("dimension")
        semantic_candidates = {
            item.profile_kind
            for item in direct
            if item.profile_kind in {"metric", "dimension", "business_rule", "relationship", "policy"}
        }
        candidates: dict[str, list[tuple[float, int, RankedResult]]] = defaultdict(list)
        for rank, result in enumerate(direct):
            candidates[result.profile_kind].append((self._kind_match_score(question, result), rank, result))
        if requested_kinds == {"metric", "dimension"} and not any(
            score > 0 for score, _, _ in candidates.get("metric", [])
        ):
            requested_kinds.add("entity")
        # A business rule whose name is strongly covered by the question (score >= 1.0 means
        # every one of the rule's own name terms — or the whole query — was matched, not just an
        # incidental shared word like "fraud" appearing in an unrelated rule name) must never be
        # suppressed by the coarse rule_intent/requested_kinds keyword gates below — those gates
        # exist only to pull business_rule in when it has no strong direct evidence of its own,
        # not to hide one that does.
        _STRONG_MATCH = 1.0
        rule_has_direct_match = any(score >= _STRONG_MATCH for score, _, _ in candidates.get("business_rule", []))
        for kind, values in candidates.items():
            if requested_kinds and kind not in requested_kinds and kind != "legacy_concept":
                if not (kind == "business_rule" and rule_has_direct_match):
                    continue
            if kind == "business_rule" and not rule_intent and not rule_has_direct_match:
                continue
            if kind == "relationship" and not explicit_relationship:
                continue
            if kind == "entity" and not requested_kinds and not explicit_entity and semantic_candidates:
                continue
            if kind == "physical_table" and not requested_kinds and not explicit_table and semantic_candidates:
                continue
            values.sort(key=lambda item: (-item[0], item[1]))
            # Beyond the top-ranked candidate, only admit additional same-kind objects that have
            # genuine name-term overlap with the question — otherwise a raised cap would let in
            # same-kind noise that merely rode along in the top-10 search results.
            take = values[:1] + [item for item in values[1:limits.get(kind, 0)] if item[0] >= _STRONG_MATCH]
            seeds.extend(item.id for _, _, item in take)
        if not seeds and ranked:
            seeds.append(ranked[0].id)

        selected = set(seeds)
        intent_entities: set[str] = set()
        for object_id in list(seeds):
            obj = self.by_id[object_id]
            spec = obj.cerebro
            if obj.profile_kind == "entity":
                intent_entities.add(obj.id)
                selected.add(str(spec.get("physical_mapping", {}).get("table", "")))
            elif obj.profile_kind == "dimension":
                entity = str(spec.get("entity", ""))
                intent_entities.add(entity)
                selected.add(entity)
                selected.update(str(item.get("table", "")) for item in spec.get("physical_mappings", []) if isinstance(item, dict))
            elif obj.profile_kind == "metric":
                entity = str(spec.get("entity", ""))
                intent_entities.add(entity)
                selected.add(entity)
                selected.update(str(item) for item in spec.get("dependencies", []))
                if spec.get("time_dimension"):
                    selected.add(str(spec["time_dimension"]))
            elif obj.profile_kind == "business_rule":
                entity = str(spec.get("entity", ""))
                intent_entities.add(entity)
                selected.add(entity)
                selected.update(str(item) for item in spec.get("dependencies", []))
            elif obj.profile_kind == "relationship":
                self._add_relationship_closure(obj, selected, intent_entities)
            elif obj.profile_kind in {"policy", "legacy_concept"}:
                selected.update(str(item) for item in obj.links)

        # Entities pulled in by metrics, dimensions, and rules contribute only their table mapping.
        for entity_id in list(intent_entities | {item for item in selected if item.startswith("entity.")}):
            entity = self.by_id.get(entity_id)
            if entity and entity.profile_kind == "entity":
                selected.add(str(entity.cerebro.get("physical_mapping", {}).get("table", "")))

        # Connect intent entities through the shortest reviewed relationship paths.
        entities = sorted(entity for entity in intent_entities if entity in self.by_id)
        for index, source in enumerate(entities):
            for target in entities[index + 1:]:
                for relationship_id in self._shortest_relationship_path(source, target):
                    relationship = self.by_id[relationship_id]
                    selected.add(relationship_id)
                    self._add_relationship_closure(relationship, selected, intent_entities)
        selected.discard("")
        return sorted(item for item in selected if item in self.by_id)

    def _kind_match_score(self, question: str, result: RankedResult) -> float:
        normalized = question.lower()
        if result.profile_kind == "dimension" and " by " in normalized:
            normalized = normalized.rsplit(" by ", 1)[1]
        elif result.profile_kind == "metric" and " by " in normalized:
            normalized = normalized.split(" by ", 1)[0]
        generic = {
            "entity", "dimension", "metric", "rule", "relationship", "join", "policy",
            "physical", "mapping", "schema", "table", "using", "monthly",
        }
        query_terms = set(_tokens(normalized)) - generic
        obj = self.by_id[result.id]
        names = [obj.name, *obj.aliases]
        best = 0.0
        for name in names:
            name_terms = set(_tokens(name))
            if not name_terms:
                continue
            overlap = query_terms & name_terms
            score = len(overlap) / len(name_terms)
            score += len(overlap) / len(query_terms) if query_terms else 0.0
            if name_terms == query_terms:
                score += 2.0
            best = max(best, score)
        return best

    def _add_relationship_closure(
        self,
        relationship: SemanticObject,
        selected: set[str],
        entities: set[str],
    ) -> None:
        semantic = relationship.cerebro.get("semantic", {})
        if isinstance(semantic, dict):
            for key in ("from", "to"):
                entity_id = str(semantic.get(key, ""))
                if entity_id:
                    entities.add(entity_id)
                    selected.add(entity_id)
        physical = relationship.cerebro.get("physical", {})
        if isinstance(physical, dict):
            for key in ("source", "target"):
                binding = physical.get(key, {})
                if isinstance(binding, dict) and binding.get("table"):
                    selected.add(str(binding["table"]))
        selected.update(str(item) for item in relationship.links if str(item).startswith("table."))

    def _shortest_relationship_path(self, source: str, target: str) -> list[str]:
        graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for obj in self.bundle.objects:
            if obj.profile_kind != "relationship":
                continue
            semantic = obj.cerebro.get("semantic", {})
            if not isinstance(semantic, dict):
                continue
            left, right = str(semantic.get("from", "")), str(semantic.get("to", ""))
            if left in self.by_id and right in self.by_id:
                graph[left].append((right, obj.id))
                graph[right].append((left, obj.id))
        queue: list[tuple[str, list[str]]] = [(source, [])]
        visited = {source}
        while queue:
            current, path = queue.pop(0)
            if current == target:
                return path
            for neighbor, relationship_id in sorted(graph.get(current, [])):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, [*path, relationship_id]))
        return []

    @staticmethod
    def _summary(obj: SemanticObject) -> dict[str, object]:
        return {
            "id": obj.id,
            "name": obj.name,
            "description": obj.description,
            "profile_kind": obj.profile_kind,
            "cerebro": obj.cerebro,
        }


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


# ---------------------------------------------------------------------------
# Authorization-first retrieval and immutable grounding snapshots (Task 3).
#
# The advisory methods above stay unchanged: `/api/grounding` and the MCP tools
# keep returning metadata-only `GroundingResponse`. Everything below is a
# separate, scope-first path that never ranks or traverses an unauthorized
# object and freezes its result as a value-free `GroundingSnapshot`.
# ---------------------------------------------------------------------------


class AuthorizationScopeIntegrityError(Exception):
    """Raised when a scope's declared hash does not match its payload."""


class SnapshotMetadataError(Exception):
    """Raised when governed metadata cannot be normalized without guessing."""


class UnsupportedDialectError(Exception):
    """Raised for any dialect other than the single supported `duckdb`."""


# Declared DuckDB types normalize into the versioned scalar registry. Anything
# absent here fails closed rather than being guessed into a scalar type.
_DECLARED_TYPE_TO_SCALAR: dict[str, str] = {
    "BIGINT": "integer",
    "INTEGER": "integer",
    "INT": "integer",
    "SMALLINT": "integer",
    "TINYINT": "integer",
    "HUGEINT": "integer",
    "UBIGINT": "integer",
    "UINTEGER": "integer",
    "USMALLINT": "integer",
    "UTINYINT": "integer",
    "DOUBLE": "decimal",
    "FLOAT": "decimal",
    "REAL": "decimal",
    "DECIMAL": "decimal",
    "NUMERIC": "decimal",
    "VARCHAR": "string",
    "TEXT": "string",
    "STRING": "string",
    "CHAR": "string",
    "UUID": "string",
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "DATETIME": "timestamp",
}
_SCALAR_TYPES = frozenset(
    {"string", "integer", "decimal", "boolean", "date", "timestamp"}
)


def _normalized_scalar_type(declared: object, subject: str) -> str:
    if not isinstance(declared, str) or not declared.strip():
        raise SnapshotMetadataError(f"{subject} has no declared type")
    key = declared.strip().upper().split("(", 1)[0]
    scalar = _DECLARED_TYPE_TO_SCALAR.get(key)
    if scalar is None:
        raise SnapshotMetadataError(f"{subject} declares an unsupported type")
    return scalar


def _declared_metric_result_type(metadata: dict[str, object], object_id: str) -> str:
    declared = metadata.get("metric_result_type")
    if not isinstance(declared, str) or declared not in _SCALAR_TYPES:
        # Never infer a metric result type from a formula: an ungoverned guess
        # would silently change what downstream type checking accepts.
        raise SnapshotMetadataError(
            f"{object_id} does not declare a governed metric result type"
        )
    return declared


class GroundingResolver:
    """Resolve a canonical question into an immutable, authorized snapshot."""

    def __init__(
        self,
        retriever: SemanticRetriever,
        *,
        retrieval_config_hash: str,
        top_k: int = 10,
        depth: int = 1,
    ) -> None:
        self.retriever = retriever
        self.retrieval_config_hash = retrieval_config_hash
        self.top_k = top_k
        self.depth = depth

    # -- authorization boundary ------------------------------------------
    def _is_authorized(self, obj: SemanticObject, scope: AuthorizationScope) -> bool:
        if obj.id not in scope.allowed_object_ids:
            return False
        if obj.status not in {"active", "stable"}:
            return False
        classification = str(obj.cerebro.get("classification", "internal"))
        return classification in scope.allowed_classifications

    def authorized_candidates(
        self, scope: AuthorizationScope
    ) -> tuple[SemanticObject, ...]:
        """Return the only objects retrieval may ever see, before ranking."""
        return tuple(
            obj
            for obj in self.retriever.bundle.objects
            if self._is_authorized(obj, scope)
        )

    def expand_authorized(
        self,
        seed_ids: tuple[str, ...] | list[str],
        scope: AuthorizationScope,
        depth: int = 1,
    ) -> tuple[str, ...]:
        """Expand the graph without ever entering an unauthorized node."""
        allowed = {obj.id for obj in self.authorized_candidates(scope)}
        visited = {object_id for object_id in seed_ids if object_id in allowed}
        frontier = set(visited)
        for _ in range(max(0, depth)):
            neighbors = {
                neighbor
                for item in frontier
                for neighbor in self.retriever.adjacency.get(item, set())
                if neighbor in allowed
            }
            frontier = neighbors - visited
            if not frontier:
                break
            visited.update(frontier)
        return tuple(sorted(visited))

    def _ranked_authorized(
        self, canonical_question: str, scope: AuthorizationScope
    ) -> list[tuple[str, float, list[str]]]:
        allowed = {obj.id for obj in self.authorized_candidates(scope)}
        terms = _tokens(canonical_question)
        ranked: list[tuple[str, float, list[str]]] = []
        for object_id in sorted(allowed):
            haystack = _tokens(self.retriever.documents.get(object_id, ""))
            counts = {term: haystack.count(term) for term in set(terms)}
            matched = sorted({term for term in terms if counts.get(term, 0)})
            if not matched:
                continue
            score = sum(1.0 + math.log1p(counts[term]) for term in matched)
            ranked.append(
                (object_id, round(score, 8), [f"lexical:{term}" for term in matched])
            )
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return ranked[: self.top_k]

    # -- snapshot construction -------------------------------------------
    def _snapshot_columns(
        self, obj: SemanticObject, scope: AuthorizationScope
    ) -> tuple[SnapshotColumn, ...]:
        columns: list[SnapshotColumn] = []
        for column in obj.cerebro.get("columns", []) or []:
            if not isinstance(column, dict):
                raise SnapshotMetadataError(f"{obj.id} declares a malformed column")
            name = column.get("name")
            if not isinstance(name, str) or not name:
                raise SnapshotMetadataError(f"{obj.id} declares an unnamed column")
            classification = str(column.get("classification", "internal"))
            if classification not in scope.allowed_classifications:
                continue
            columns.append(
                SnapshotColumn(
                    ref=ColumnRef(table_id=obj.id, column=name),
                    data_type=_normalized_scalar_type(
                        column.get("data_type"), f"{obj.id}.{name}"
                    ),
                    description=str(column.get("description", "")),
                    classification=classification,
                )
            )
        return tuple(columns)

    def _snapshot_relationships(
        self, obj: SemanticObject, member_ids: frozenset[str]
    ) -> tuple[SnapshotRelationship, ...]:
        """Return only edges this snapshot can stand behind on its own.

        `member_ids` are the objects this snapshot actually contains, not merely
        the ones the scope authorizes. An edge to a table the snapshot omits
        would advertise a join whose endpoint the consumer cannot see, and it
        would leak that the omitted table exists.
        """
        relationships: list[SnapshotRelationship] = []
        for candidate_id in sorted(set(obj.links) | {obj.id}):
            candidate = self.retriever.by_id.get(candidate_id)
            if candidate is None or candidate.profile_kind != "relationship":
                continue
            if candidate.id not in member_ids:
                continue
            metadata = candidate.cerebro
            physical = metadata.get("physical", {})
            physical = physical if isinstance(physical, dict) else {}
            source_binding = physical.get("source", {})
            target_binding = physical.get("target", {})
            source_binding = source_binding if isinstance(source_binding, dict) else {}
            target_binding = target_binding if isinstance(target_binding, dict) else {}
            source_table = str(metadata.get("source_table") or source_binding.get("table") or "")
            target_table = str(metadata.get("target_table") or target_binding.get("table") or "")
            source_column = str(metadata.get("source_column") or source_binding.get("column") or "")
            target_column = str(metadata.get("target_column") or target_binding.get("column") or "")
            # Both endpoints must survive authorization, otherwise the edge
            # would leak the existence of an unauthorized table.
            if source_table not in member_ids or target_table not in member_ids:
                continue
            if not source_column or not target_column:
                raise SnapshotMetadataError(
                    f"{candidate.id} does not declare both join columns"
                )
            relationships.append(
                SnapshotRelationship(
                    relationship_id=candidate.id,
                    left=ColumnRef(table_id=source_table, column=source_column),
                    right=ColumnRef(table_id=target_table, column=target_column),
                )
            )
        return tuple(relationships)

    @staticmethod
    def _snapshot_warnings(obj: SemanticObject) -> tuple[SnapshotWarning, ...]:
        warnings: list[SnapshotWarning] = []
        for warning in obj.cerebro.get("warnings", []) or []:
            text = str(warning)
            if not text:
                continue
            # Prose carries no actionable authority: it becomes a stable hash
            # with no control ID until a governed control registry exists.
            warnings.append(
                SnapshotWarning(
                    object_id=obj.id,
                    warning_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    kind="informational",
                    control_id="",
                )
            )
        return tuple(warnings)

    @staticmethod
    def _governed_literals(
        obj: SemanticObject,
    ) -> tuple[SnapshotGovernedLiteral, ...]:
        declared = obj.cerebro.get("governed_literals", []) or []
        if not isinstance(declared, list):
            raise SnapshotMetadataError(
                f"{obj.id} declares malformed governed literals"
            )
        literals: list[SnapshotGovernedLiteral] = []
        for entry in declared:
            # Only an exact authored declaration counts. No discovery, sample,
            # or config-derived value may ever populate a governed literal.
            if not isinstance(entry, dict):
                raise SnapshotMetadataError(
                    f"{obj.id} declares a malformed governed literal"
                )
            if set(entry) != {"literal_id", "data_type", "value"}:
                raise SnapshotMetadataError(
                    f"{obj.id} governed literals require exactly ID, type, and value"
                )
            literals.append(
                SnapshotGovernedLiteral(
                    literal_id=str(entry["literal_id"]),
                    data_type=str(entry["data_type"]),
                    value=entry["value"],
                    source_object_id=obj.id,
                )
            )
        return tuple(literals)

    def _snapshot_object(
        self,
        obj: SemanticObject,
        scope: AuthorizationScope,
        member_ids: frozenset[str],
    ) -> SnapshotMetadataObject:
        formula: str | None = None
        metric_result_type: str | None = None
        object_type = {
            "physical_table": "table",
            "legacy_concept": "concept",
        }.get(obj.profile_kind, obj.profile_kind)
        if obj.profile_kind == "metric":
            declared_formula = obj.cerebro.get("formula")
            if not isinstance(declared_formula, str) or not declared_formula.strip():
                raise SnapshotMetadataError(f"{obj.id} declares no governed formula")
            formula = declared_formula
            metric_result_type = _declared_metric_result_type(obj.cerebro, obj.id)
        return SnapshotMetadataObject(
            object_id=obj.id,
            object_type=object_type,
            description=obj.description,
            columns=self._snapshot_columns(obj, scope) if obj.profile_kind == "physical_table" else (),
            formula=formula,
            metric_result_type=metric_result_type,
            relationships=self._snapshot_relationships(obj, member_ids),
            warnings=self._snapshot_warnings(obj),
        )

    def resolve(
        self,
        canonical_question: str,
        scope: AuthorizationScope,
        dialect: str,
    ) -> GroundingSnapshot:
        """Freeze an authorized, value-free snapshot for one canonical question."""
        if not isinstance(scope, AuthorizationScope):
            raise TypeError("scope must be a trusted AuthorizationScope")
        if dialect != "duckdb":
            raise UnsupportedDialectError("only the duckdb dialect is supported")
        if canonicalize_question(canonical_question) != canonical_question:
            raise ValueError("question must already be canonical")
        # Integrity first: a scope whose hash does not match its payload never
        # reaches retrieval. This proves integrity, not caller authentication.
        if authorization_scope_sha256(scope) != scope.authorization_scope_hash:
            raise AuthorizationScopeIntegrityError(
                "authorization scope hash does not match its payload"
            )

        candidates = self.authorized_candidates(scope)
        ranked = self._ranked_authorized(canonical_question, scope)
        selected_ids = set(
            self.expand_authorized(
                tuple(object_id for object_id, _, _ in ranked), scope, self.depth
            )
        )
        selected = tuple(obj for obj in candidates if obj.id in selected_ids)

        member_ids = frozenset(obj.id for obj in selected)
        objects = tuple(
            self._snapshot_object(obj, scope, member_ids) for obj in selected
        )
        governed_literals = tuple(
            literal for obj in selected for literal in self._governed_literals(obj)
        )
        ranking_evidence = tuple(
            SnapshotRankingEvidence(
                object_id=object_id,
                rank=index,
                score=score,
                signal_codes=("lexical",) if reasons else (),
            )
            for index, (object_id, score, reasons) in enumerate(ranked, 1)
            if object_id in selected_ids
        )

        draft = GroundingSnapshot(
            snapshot_version=GROUNDING_SNAPSHOT_VERSION,
            semantic_version=self.retriever.bundle.version,
            policy_version=scope.policy_version,
            canonicalization_version=QUESTION_CANONICALIZATION_VERSION,
            literal_registry_version=LITERAL_SPAN_REGISTRY_VERSION,
            type_registry_version=EXPRESSION_TYPE_REGISTRY_VERSION,
            authorization_scope_hash=scope.authorization_scope_hash,
            retrieval_config_hash=self.retrieval_config_hash,
            dialect="duckdb",
            objects=objects,
            governed_literals=governed_literals,
            ranking_evidence=ranking_evidence,
            authorized_object_ids=frozenset(item.object_id for item in objects),
            policy_ids=frozenset(
                item.object_id for item in objects if item.object_type == "policy"
            ),
            dialect_capabilities=DialectCapabilities(
                supports_window=True, supports_set_operations=True
            ),
            snapshot_hash="0" * 64,
        )
        return draft.model_copy(
            update={"snapshot_hash": grounding_snapshot_sha256(draft)}
        )
