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
    description: str = ""
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
    source_mode: Literal["configured", "database_only"] = "configured"
    source_name: str
    source_version: str
    database_path: str
    schema_name: str
    tables: list[TableFact]
    relationships: list[RelationshipFact]
    rules: list[dict[str, Any]] = Field(default_factory=list)
    schema_reference: Provenance
    row_sampling: Literal["disabled"] = "disabled"
    discovery_evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def column_count(self) -> int:
        return sum(len(table.columns) for table in self.tables)


class ConceptCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: list[str]
    classification: Literal["public", "internal", "confidential", "restricted"]
    maps_to: list[str] = Field(min_length=1)
    warnings: list[str]


class PolicyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    classification: Literal["public", "internal", "confidential", "restricted"]
    applies_to: list[str] = Field(min_length=1)
    rule: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(min_length=1)
    warnings: list[str]


class BusinessSemantics(BaseModel):
    table_purposes: dict[str, str]
    concepts: list[ConceptCandidate]
    policies: list[PolicyCandidate] = Field(default_factory=list)
    classifications: dict[str, str]


class MetricCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    classification: Literal["public", "internal", "confidential", "restricted"]
    formula: str = Field(min_length=1)
    dependencies: list[str] = Field(min_length=1)
    filters: list[str]
    grain: str = Field(min_length=1)
    warnings: list[str]


class QuerySemantics(BaseModel):
    grains: dict[str, str]
    dimensions: list[dict[str, Any]]
    measures: list[MetricCandidate]
    joins: list[dict[str, Any]]
    guidance: list[str]
    warnings: list[str]


class RelationshipCandidate(BaseModel):
    id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    cardinality: Literal["one-to-one", "one-to-many", "many-to-one", "many-to-many"]
    description: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class RelationshipSemantics(BaseModel):
    relationships: list[RelationshipCandidate] = Field(default_factory=list)


class SemanticProposal(BaseModel):
    business: BusinessSemantics
    relationships: RelationshipSemantics = Field(default_factory=RelationshipSemantics)
    query: QuerySemantics
    generation_mode: Literal["live", "fallback"]
    provider: str
    model: str


GenerationStage = Literal[
    "source_check",
    "catalog_scan",
    "business_semantics",
    "relationship_semantics",
    "query_semantics",
    "compile_okf",
    "validate_candidate",
    "candidate_ready",
]


class GenerationEvent(BaseModel):
    sequence: int
    stage: GenerationStage
    status: Literal["started", "completed", "skipped", "failed"]
    summary: str
    command: str
    timestamp: str
    details: dict[str, Any] = Field(default_factory=dict)


class GenerationCandidate(BaseModel):
    name: str
    version: str
    counts: dict[str, int]
    generation_mode: Literal["live", "fallback"]
    provider: str | None = None
    model: str | None = None
    source_mode: Literal["configured", "database_only"] = "configured"
    discovery_evidence: dict[str, Any] = Field(default_factory=dict)
    review_state: Literal["candidate", "approved", "rejected"] = "candidate"
    review_record: dict[str, Any] | None = None


class GenerationRun(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str
    updated_at: str
    events: list[GenerationEvent] = Field(default_factory=list)
    candidate: GenerationCandidate | None = None
    error: dict[str, str] | None = None
    source_mode: Literal["configured", "database_only"] = "configured"


class GenerationStartRequest(BaseModel):
    source_mode: Literal["configured", "database_only"] = "configured"


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=200)
    comment: str = Field(default="", max_length=4000)
    acknowledge_ai_risk: bool = False


class ReviewRecord(BaseModel):
    run_id: str
    decision: Literal["approve", "reject"]
    reviewer: str
    comment: str = ""
    acknowledge_ai_risk: bool
    reviewed_at: str
    candidate_digest: str
    reviewed_bundle: str | None = None
    reviewed_digest: str | None = None


class QueryPlan(BaseModel):
    intent: str
    requires_query: bool = True
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    clarification: str | None = None


class SQLProposal(BaseModel):
    sql: str
    explanation: str = ""


class AnswerPayload(BaseModel):
    answer: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)


class AgentTrace(BaseModel):
    agent: str
    status: Literal["completed", "blocked", "skipped"]
    summary: str


class ChatResponse(BaseModel):
    conversation_id: str
    status: Literal["answered", "clarification", "blocked"]
    answer: str
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    semantic_version: str
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: list[AgentTrace] = Field(default_factory=list)


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
    generation_mode: str = "fallback"
    review_state: str = "active"
    provider: str | None = None
    model: str | None = None
    source_mode: Literal["configured", "database_only"] = "configured"
    discovery_evidence: dict[str, Any] = Field(default_factory=dict)

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
