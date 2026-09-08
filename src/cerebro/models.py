from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    origin: Literal["discovered", "declared", "ai_proposed", "human_reviewed", "derived"]
    source: str


class ColumnFact(BaseModel):
    name: str
    data_type: str
    nullable: bool
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    provenance: Provenance


class TableFact(BaseModel):
    name: str
    schema_name: str
    description: str = ""
    grain: str = ""
    aliases: list[str] = Field(default_factory=list)
    primary_key: str | None = None
    columns: list[ColumnFact]
    provenance: dict[str, Provenance]


class RelationshipFact(BaseModel):
    id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    cardinality: Literal["one-to-one", "one-to-many", "many-to-one", "many-to-many"]
    provenance: Provenance


class CatalogSnapshot(BaseModel):
    source_name: str
    source_version: str
    database_path: str
    schema_name: str
    tables: list[TableFact]
    relationships: list[RelationshipFact]
    rules: list[dict[str, Any]] = Field(default_factory=list)
    schema_reference: Provenance
    row_sampling: Literal["disabled"] = "disabled"

    @property
    def column_count(self) -> int:
        return sum(len(table.columns) for table in self.tables)


class BusinessSemantics(BaseModel):
    table_purposes: dict[str, str]
    concepts: list[dict[str, Any]]
    classifications: dict[str, str]


class QuerySemantics(BaseModel):
    grains: dict[str, str]
    dimensions: list[dict[str, Any]]
    measures: list[dict[str, Any]]
    joins: list[dict[str, Any]]
    guidance: list[str]
    warnings: list[str]


class SemanticProposal(BaseModel):
    business: BusinessSemantics
    query: QuerySemantics
    generation_mode: Literal["live", "fallback"]
    provider: str
    model: str


class SemanticObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: Literal["dataset", "table", "concept", "relationship", "metric", "policy"]
    name: str
    description: str = ""
    status: Literal["active", "draft", "deprecated"] = "active"
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    cerebro: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    path: str = ""


class SemanticBundle(BaseModel):
    name: str
    version: str
    root: str
    objects: list[SemanticObject]

    def by_id(self) -> dict[str, SemanticObject]:
        return {obj.id: obj for obj in self.objects}


class ValidationIssue(BaseModel):
    code: str
    message: str
    path: str = ""


class ValidationReport(BaseModel):
    valid: bool
    document_count: int
    issues: list[ValidationIssue] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    description: str = ""
    classification: str = "internal"


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    label: str = ""


class GraphResponse(BaseModel):
    version: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class RankedResult(BaseModel):
    id: str
    type: str
    name: str
    score: float
    evidence: list[str]


class GroundingResponse(BaseModel):
    semantic_version: str
    retrieval_mode: Literal["lexical_graph", "hybrid_graph"]
    question: str
    concepts: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    columns: list[str]
    joins: list[dict[str, Any]]
    grain: list[str]
    metrics: list[dict[str, Any]]
    filters: list[Any]
    warnings: list[str]
    classifications: list[str]
    provenance: list[dict[str, Any]]
    ranking_evidence: list[RankedResult]


# Executable query contracts are separate from advisory Phase 1 metadata.
from .query_models import (  # noqa: E402,F401
    SQLGenerationRequest, SQLGenerationResponse, OkResponse, CheckFailedResponse,
    RefusedResponse, QueryResult, CheckViolation, RelationalQueryIR,
    AuthorizationScope, GroundingSnapshot,
)
