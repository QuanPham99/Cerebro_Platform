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
            targets = list(obj.links)
            targets += [str(value) for value in obj.cerebro.get("dependencies", [])]
            targets += [str(value) for value in obj.cerebro.get("applies_to", [])]
            targets += [str(value) for value in obj.cerebro.get("maps_to", [])]
            for target in targets:
                if target not in self.by_id:
                    continue
                # Canonical parent/policy/relationship objects already emit these
                # connections. Omitting reverse table links keeps the graph legible.
                if obj.type == "table" and self.by_id[target].type in {"dataset", "relationship", "policy"}:
                    continue
                edge_type = "semantic_mapping"
                if obj.type == "metric":
                    edge_type = "metric_dependency"
                elif obj.type == "policy":
                    edge_type = "policy_coverage"
                key = (obj.id, target, edge_type)
                if key in seen:
                    continue
                seen.add(key)
                edge = GraphEdge(id=f"{obj.id}->{target}", source=obj.id, target=target, type=edge_type, label=edge_type.replace("_", " "))
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

    def __init__(self, model: str = "text-embedding-3-small"):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model

    def __call__(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


def embedder_from_environment() -> OpenAIEmbedder | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        return OpenAIEmbedder(os.getenv("CEREBRO_EMBEDDING_MODEL", "text-embedding-3-small"))
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
        if obj.status != "active":
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
            if candidate is None or candidate.type != "relationship":
                continue
            if candidate.id not in member_ids:
                continue
            metadata = candidate.cerebro
            source_table = str(metadata.get("source_table", ""))
            target_table = str(metadata.get("target_table", ""))
            source_column = str(metadata.get("source_column", ""))
            target_column = str(metadata.get("target_column", ""))
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
        if obj.type == "metric":
            declared_formula = obj.cerebro.get("formula")
            if not isinstance(declared_formula, str) or not declared_formula.strip():
                raise SnapshotMetadataError(f"{obj.id} declares no governed formula")
            formula = declared_formula
            metric_result_type = _declared_metric_result_type(obj.cerebro, obj.id)
        return SnapshotMetadataObject(
            object_id=obj.id,
            object_type=obj.type,
            description=obj.description,
            columns=self._snapshot_columns(obj, scope) if obj.type == "table" else (),
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
