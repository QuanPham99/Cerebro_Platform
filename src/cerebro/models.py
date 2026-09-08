from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .semantic.profile import normalize_profile_kind


Classification = Literal["public", "internal", "confidential", "restricted"]


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


class PhysicalColumnBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1)
    column: str = Field(min_length=1)


class EntityPhysicalMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1)
    key: list[str] = Field(min_length=1)


class GrainDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    key: list[str] = Field(default_factory=list)


class MetricPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PhysicalColumnBinding
    operator: Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "is_null", "not_null"]
    value: Any | None = None


class AggregateMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["aggregate"] = "aggregate"
    aggregation: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    source: PhysicalColumnBinding | None = None
    predicates: list[MetricPredicate] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_source_for_column_aggregates(self) -> "AggregateMeasure":
        if self.aggregation != "count" and self.source is None:
            raise ValueError(f"{self.aggregation} requires a source column")
        return self


class RatioMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ratio"] = "ratio"
    numerator: AggregateMeasure
    denominator: AggregateMeasure
    scale: float = 100.0


MetricMeasure = Annotated[AggregateMeasure | RatioMeasure, Field(discriminator="kind")]


class EntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    classification: Classification
    physical_mapping: EntityPhysicalMapping
    grain: GrainDefinition
    warnings: list[str] = Field(default_factory=list)


class DimensionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    physical_mappings: list[PhysicalColumnBinding] = Field(min_length=1)
    semantic_type: Literal["categorical", "temporal", "numeric", "geographic", "derived"]
    derivation: str | None = None
    compatible_metrics: list[str] = Field(default_factory=list)
    classification: Classification
    warnings: list[str] = Field(default_factory=list)


class StructuredMetricCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    measure: MetricMeasure
    dependencies: list[str] = Field(min_length=1)
    grain: GrainDefinition
    compatible_dimensions: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    relative_time_anchor: Literal["max_available_date"] | None = None
    classification: Classification
    warnings: list[str] = Field(default_factory=list)


class BusinessRuleCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    rule_kind: Literal["predicate", "classification", "time_anchor", "aggregation_constraint"]
    output_type: Literal["boolean", "category", "direction", "date"]
    dependencies: list[str] = Field(min_length=1)
    logic: str = Field(min_length=1)
    grain: GrainDefinition
    classification: Classification
    warnings: list[str] = Field(default_factory=list)


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
    model_config = ConfigDict(extra="forbid")

    table_purposes: dict[str, str] = Field(default_factory=dict)
    concepts: list[ConceptCandidate] = Field(default_factory=list)
    entities: list[EntityCandidate] = Field(default_factory=list)
    dimensions: list[DimensionCandidate] = Field(default_factory=list)
    policies: list[PolicyCandidate] = Field(default_factory=list)
    classifications: dict[str, str] = Field(default_factory=dict)


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
    model_config = ConfigDict(extra="forbid")

    grains: dict[str, str] = Field(default_factory=dict)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    measures: list[MetricCandidate] = Field(default_factory=list)
    structured_measures: list[StructuredMetricCandidate] = Field(default_factory=list)
    rules: list[BusinessRuleCandidate] = Field(default_factory=list)
    joins: list[dict[str, Any]] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RelationshipCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    cardinality: Literal["one-to-one", "one-to-many", "many-to-one", "many-to-many"]
    source_entity: str | None = None
    target_entity: str | None = None
    description: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class RelationshipSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationships: list[RelationshipCandidate] = Field(default_factory=list)


class SemanticProposal(BaseModel):
    business: BusinessSemantics
    relationships: RelationshipSemantics = Field(default_factory=RelationshipSemantics)
    query: QuerySemantics
    generation_mode: Literal["live", "fallback", "partial", "authored"]
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
    status: Literal["started", "completed", "skipped", "failed", "degraded"]
    summary: str
    command: str
    timestamp: str
    details: dict[str, Any] = Field(default_factory=dict)


class GenerationTraceStep(BaseModel):
    stage: GenerationStage
    actor: Literal["source", "agent", "compiler", "validator", "system"]
    agent_id: str | None = None
    status: Literal["running", "completed", "skipped", "failed", "degraded"]
    started_at: str | None = None
    completed_at: str | None = None
    summary: str = ""
    command: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: dict[str, str] | None = None


class GenerationTrace(BaseModel):
    run_id: str
    steps: list[GenerationTraceStep] = Field(default_factory=list)


class GenerationCandidate(BaseModel):
    name: str
    version: str
    counts: dict[str, int]
    generation_mode: Literal["live", "fallback", "partial", "authored"]
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


class MetricDefinitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric"]
    definition: StructuredMetricCandidate


class BusinessRuleDefinitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["business_rule"]
    definition: BusinessRuleCandidate


DefinitionPayload = Annotated[
    MetricDefinitionPayload | BusinessRuleDefinitionPayload,
    Field(discriminator="kind"),
]


class DefinitionTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric", "business_rule"]
    intent: str = Field(min_length=1, max_length=4000)
    entity_id: str | None = None


class DefinitionTranslation(BaseModel):
    payload: DefinitionPayload
    warnings: list[str] = Field(default_factory=list)
    provider: str
    model: str


class DefinitionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: DefinitionPayload
    origin: Literal["ai_proposed", "declared"] = "declared"


class DefinitionRevision(BaseModel):
    id: str
    base_version: str
    version: str
    counts: dict[str, int]
    generation_mode: Literal["authored"] = "authored"
    review_state: Literal["candidate", "approved", "rejected"] = "candidate"
    review_record: dict[str, Any] | None = None


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
    type: str = Field(min_length=1)
    name: str
    title: str = ""
    profile_kind: str = "generic"
    description: str = ""
    resource: str | None = None
    status: Literal["stable", "draft", "deprecated"] = "stable"
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    generated: dict[str, Any] | None = None
    verified: list[dict[str, Any]] = Field(default_factory=list)
    stale_after: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    cerebro: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    path: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_okf_compatibility(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        title = str(data.get("title") or data.get("name") or data.get("id") or "")
        data.setdefault("title", title)
        data.setdefault("name", title)
        if data.get("status") == "active":
            data["status"] = "stable"
        verified = data.get("verified")
        if isinstance(verified, dict):
            data["verified"] = [verified]
        data.setdefault("profile_kind", normalize_profile_kind(data.get("type"), data.get("cerebro")))
        return data


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
    okf_version: str | None = None
    semantic_profile_version: str | None = None

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
    profile_kind: str = "generic"


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
    profile_kind: str = "generic"
    score: float
    evidence: list[str]


class GroundingResponse(BaseModel):
    semantic_version: str
    retrieval_mode: Literal["lexical_graph", "hybrid_graph"]
    question: str
    concepts: list[dict[str, Any]]
    entities: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
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
