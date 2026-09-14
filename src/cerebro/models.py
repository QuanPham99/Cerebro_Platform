from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from datetime import date as _Date
from datetime import datetime as _DateTime
from decimal import Decimal
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    TypeAdapter,
    field_serializer,
    model_validator,
)

from .semantic.profile import normalize_profile_kind


Classification = Literal["public", "internal", "confidential", "restricted"]


class Provenance(BaseModel):
    origin: Literal[
        "discovered", "declared", "ai_proposed", "human_reviewed", "derived"
    ]
    source: str


class ColumnFact(BaseModel):
    name: str
    data_type: str
    nullable: bool
    description: str = ""
    classification: Literal["public", "internal", "confidential", "restricted"] = (
        "internal"
    )
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


class BundleVersionSummary(BaseModel):
    id: str
    name: str
    version: str
    origin: Literal["golden", "generation", "definition"]
    is_default: bool = False
    review_state: Literal["approved"] = "approved"
    reviewer: str | None = None
    reviewed_at: str | None = None
    parent_version: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    kind_counts: dict[str, int] = Field(default_factory=dict)
    generation_mode: str
    source_mode: Literal["configured", "database_only"] = "configured"
    provider: str | None = None
    model: str | None = None


class BundleVersionCatalog(BaseModel):
    default_id: str | None = None
    default_change_allowed: bool = True
    versions: list[BundleVersionSummary] = Field(default_factory=list)


class BundleDefaultRequest(BaseModel):
    bundle_id: str = Field(min_length=1, max_length=200)


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
    base_bundle_id: str | None = Field(default=None, min_length=1, max_length=200)
    revision_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_one_definition_scope(self) -> "DefinitionTranslateRequest":
        if self.base_bundle_id and self.revision_id:
            raise ValueError("base_bundle_id and revision_id are mutually exclusive")
        return self


class DefinitionTranslation(BaseModel):
    payload: DefinitionPayload
    warnings: list[str] = Field(default_factory=list)
    provider: str
    model: str


class DefinitionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: DefinitionPayload
    origin: Literal["ai_proposed", "declared"] = "declared"


class DefinitionRevisionCreateRequest(DefinitionApplyRequest):
    base_bundle_id: str | None = Field(default=None, min_length=1, max_length=200)


class AuthoredDefinitionSummary(BaseModel):
    id: str
    name: str
    kind: Literal["metric", "business_rule"]


class DefinitionRevision(BaseModel):
    id: str
    base_bundle_id: str | None = None
    base_version: str
    version: str
    counts: dict[str, int]
    definitions: list[AuthoredDefinitionSummary] = Field(default_factory=list)
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
    request_id: UUID | None = None
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


class SaveChartRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False


class SavedChart(BaseModel):
    id: str
    question: str
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    created_at: str


class ReportPlanSection(BaseModel):
    title: str
    question: str


class ReportPlan(BaseModel):
    title: str = ""
    sections: list[ReportPlanSection] = Field(default_factory=list)


class ReportOverview(BaseModel):
    summary: str


class ReportSectionResult(BaseModel):
    id: str
    title: str
    question: str
    status: Literal["answered", "clarification", "blocked"]
    answer: str
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReportDocument(BaseModel):
    run_id: str
    request: str
    title: str
    overview: str = ""
    generated_at: str
    semantic_version: str
    status: Literal["completed", "partial", "failed"]
    sections: list[ReportSectionResult] = Field(default_factory=list)


class ReportRunRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)


class ReportEvent(BaseModel):
    sequence: int
    stage: str
    status: Literal["started", "completed", "clarification", "blocked", "failed"]
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ReportRun(BaseModel):
    run_id: str
    request: str
    status: Literal["running", "completed", "partial", "failed", "cancelled"]
    started_at: str
    completed_at: str | None = None
    current_stage: str | None = None
    error: str | None = None
    events: list[ReportEvent] = Field(default_factory=list)


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
    manifest_metadata: dict[str, Any] = Field(default_factory=dict)

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


# Text-to-SQL evidence contracts. These are additive so legacy semantic and
# advisory models above retain their existing validation behavior.
RawTableName: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"),
]
TableId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^table\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"),
]
Sha256Digest: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
EvidenceIdentifier: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, pattern=r"^\S(?:.*\S)?$"),
]


class _StrictFrozenEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceTableManifest(_StrictFrozenEvidenceModel):
    name: RawTableName
    file_name: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*\.csv$"),
    ]
    sha256: Sha256Digest
    row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def file_name_matches_table(self) -> SourceTableManifest:
        if self.file_name != f"{self.name}.csv":
            raise ValueError("file_name must be the raw table name plus .csv")
        return self


class SourceManifest(_StrictFrozenEvidenceModel):
    tables: tuple[SourceTableManifest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def table_names_are_unique(self) -> SourceManifest:
        names = [table.name for table in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("source manifest table names must be unique")
        return self


class MaterializedTableReceipt(_StrictFrozenEvidenceModel):
    table_id: TableId
    source_file_sha256: Sha256Digest
    row_count: int = Field(ge=0)


class MaterializationReceipt(_StrictFrozenEvidenceModel):
    source_manifest_sha256: Sha256Digest
    bundle_sha256: Sha256Digest
    tables: tuple[MaterializedTableReceipt, ...] = Field(min_length=1)
    database_sha256: Sha256Digest
    engine: Literal["duckdb"]
    engine_version: EvidenceIdentifier

    @model_validator(mode="after")
    def table_ids_are_unique(self) -> MaterializationReceipt:
        table_ids = [table.table_id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("materialization receipt table IDs must be unique")
        return self


class ProviderCapabilityReceipt(_StrictFrozenEvidenceModel):
    provider: EvidenceIdentifier
    model: EvidenceIdentifier
    revision: EvidenceIdentifier
    schema_mechanism: EvidenceIdentifier


PreflightGate: TypeAlias = Literal["data", "organizer"]
PreflightBlockerCode: TypeAlias = Literal[
    "missing_api_key",
    "missing_model",
    "missing_provider_capability",
    "invalid_provider_capability",
    "provider_identity_mismatch",
    "provider_model_mismatch",
    "provider_revision_mismatch",
    "provider_schema_mechanism_mismatch",
    "missing_data_manifest",
    "invalid_data_manifest",
    "missing_bundle",
    "missing_csv_directory",
    "source_file_set_mismatch",
    "source_file_hash_mismatch",
    "missing_materialization_receipt",
    "invalid_materialization_receipt",
    "manifest_hash_mismatch",
    "materialization_table_mismatch",
    "missing_database",
    "bundle_hash_mismatch",
    "database_hash_mismatch",
    "engine_version_mismatch",
]


_PREFLIGHT_BLOCKER_GATES: dict[PreflightBlockerCode, PreflightGate] = {
    "missing_api_key": "organizer",
    "missing_model": "organizer",
    "missing_provider_capability": "organizer",
    "invalid_provider_capability": "organizer",
    "provider_identity_mismatch": "organizer",
    "provider_model_mismatch": "organizer",
    "provider_revision_mismatch": "organizer",
    "provider_schema_mechanism_mismatch": "organizer",
    "missing_data_manifest": "data",
    "invalid_data_manifest": "data",
    "missing_bundle": "data",
    "missing_csv_directory": "data",
    "source_file_set_mismatch": "data",
    "source_file_hash_mismatch": "data",
    "missing_materialization_receipt": "data",
    "invalid_materialization_receipt": "data",
    "manifest_hash_mismatch": "data",
    "materialization_table_mismatch": "data",
    "missing_database": "data",
    "bundle_hash_mismatch": "data",
    "database_hash_mismatch": "data",
    "engine_version_mismatch": "data",
}


class PreflightBlocker(_StrictFrozenEvidenceModel):
    code: PreflightBlockerCode
    gate: PreflightGate

    @model_validator(mode="after")
    def gate_matches_code(self) -> PreflightBlocker:
        expected_gate = _PREFLIGHT_BLOCKER_GATES.get(self.code)
        if expected_gate is None or self.gate != expected_gate:
            raise ValueError("preflight blocker gate must match its code")
        return self


class PreflightReport(_StrictFrozenEvidenceModel):
    offline_ready: bool
    data_prerequisites_ready: bool
    organizer_prerequisites_ready: bool
    live_prerequisites_ready: bool
    blockers: tuple[PreflightBlocker, ...] = ()

    @model_validator(mode="after")
    def readiness_matches_blockers(self) -> PreflightReport:
        if not self.offline_ready:
            raise ValueError("offline readiness must remain available")
        data_ready = not any(blocker.gate == "data" for blocker in self.blockers)
        organizer_ready = not any(
            blocker.gate == "organizer" for blocker in self.blockers
        )
        if self.data_prerequisites_ready != data_ready:
            raise ValueError("data readiness must match data blockers")
        if self.organizer_prerequisites_ready != organizer_ready:
            raise ValueError("organizer readiness must match organizer blockers")
        if self.live_prerequisites_ready != (data_ready and organizer_ready):
            raise ValueError("live readiness must require data and organizer readiness")
        if len(self.blockers) != len(
            {(item.code, item.gate) for item in self.blockers}
        ):
            raise ValueError("preflight blockers must be unique")
        return self


# ---------------------------------------------------------------------------
# Text-to-SQL v3 contract layer.
#
# Everything below is additive: the legacy semantic/advisory models and the
# Task 0 evidence models above keep their exact fields, validators, aliases,
# `strict=True` configuration, and canonical serialization. The v3 layer uses
# its own base configurations so it never widens or narrows those boundaries.
# ---------------------------------------------------------------------------

TEXT2SQL_CONTRACT_VERSION = "008.v3"
GROUNDING_SNAPSHOT_VERSION = "008.grounding.v1"
RELATIONAL_IR_VERSION = "008.ir.v1"
QUESTION_CANONICALIZATION_VERSION = "008.question.v1"
LITERAL_SPAN_REGISTRY_VERSION = "008.literal-span.v1"
EXPRESSION_TYPE_REGISTRY_VERSION = "008.types.v1"
AUTHORIZATION_SCOPE_VERSION = "008.scope.v1"
COMPLEX_QUERY_PLAN_VERSION = "008.complex-plan.v1"

MAX_EXPRESSION_DEPTH = 32
MAX_IR_NODES = 256
MAX_EXPRESSION_ITEMS = 100
# One request performs at most two semantic calls and a fixed number of local
# gates, so an unbounded plan or attempt list is never legitimate input.
MAX_PLAN_STEPS = 16
MAX_ATTEMPT_RECORDS = 64

SUPPORTED_DEFAULT_NODE_KINDS = frozenset(
    {"scan", "join", "filter", "aggregate", "project", "sort", "limit"}
)
SUPPORTED_COMPLEX_NODE_KINDS = frozenset({"window", "set_operation"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# Identifiers and scalar types. `TableId` and `Sha256Digest` above are the
# Task 0 spellings and stay untouched; `Sha256` is the v3 alias for the same
# fail-closed lowercase-hex grammar.
Sha256: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
NodeId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
]
ColumnName: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*$"),
]
MetricId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^metric\.[a-z][a-z0-9_-]*$"),
]
RelationshipId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^relationship\.[a-z][a-z0-9_]*$"),
]
PolicyId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^policy\.[a-z][a-z0-9_-]*$"),
]
SemanticObjectId: TypeAlias = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:dataset|table|concept|relationship|metric|policy)"
        r"\.[a-z][a-z0-9_-]*$"
    ),
]
GovernedLiteralId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^literal\.[a-z][a-z0-9_-]*$"),
]
ControlId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^control\.[a-z][a-z0-9_-]*$"),
]
OptionalControlId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^(?:control\.[a-z][a-z0-9_-]*)?$"),
]
StableCode: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*$"),
]
StableSubjectId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$"),
]
SemanticStateId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_-]*$"),
]
VersionTag: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
]

Classification: TypeAlias = Literal["public", "internal", "confidential", "restricted"]
ScalarType: TypeAlias = Literal[
    "string", "integer", "decimal", "boolean", "date", "timestamp"
]
_FiniteFloat: TypeAlias = Annotated[float, Field(allow_inf_nan=False)]
JsonScalar: TypeAlias = StrictBool | StrictInt | _FiniteFloat | StrictStr | None

GenerationRoute: TypeAlias = Literal["default_ir", "planned_ir"]
ResponseGenerationRoute: TypeAlias = Literal["default_ir", "planned_ir", "none"]
CacheStatus: TypeAlias = Literal["disabled", "miss", "hit"]

AllowedFunction: TypeAlias = Literal[
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "stddev",
    "variance",
    "date_trunc",
    "nullif",
    "coalesce",
]
AllowedBinaryOperator: TypeAlias = Literal[
    "eq",
    "neq",
    "lt",
    "lte",
    "gt",
    "gte",
    "and",
    "or",
    "add",
    "subtract",
    "multiply",
    "divide",
]
ComplexOperatorId: TypeAlias = Literal[
    "window.period_over_period.v1",
    "set_operation.safe_binary.v1",
]
PipelineStage: TypeAlias = Literal[
    "snapshot",
    "cache",
    "default_ir",
    "clarification",
    "complexity",
    "planned_ir",
    "literal_resolution",
    "compile",
    "ast_check",
    "engine_validation",
    "execution",
    "provider_transport",
]

_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$")


def _sorted_ids(value: frozenset[str]) -> list[str]:
    """Serialize a set-like field deterministically without losing immutability."""
    return sorted(value)


def _check_scalar_value(data_type: str, value: Any) -> None:
    """Validate an authored value exactly against its scalar type, never coercing."""
    if data_type == "string":
        if not isinstance(value, str):
            raise ValueError("string values must be authored as text")
        return
    if data_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("boolean values must be authored as true or false")
        return
    if data_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("integer values must be authored as integers")
        return
    if data_type == "decimal":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("decimal values must be authored as numbers")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("decimal values must be finite")
        return
    if data_type == "date":
        if not isinstance(value, str) or _DATE_TEXT.match(value) is None:
            raise ValueError("date values must be authored as YYYY-MM-DD text")
        try:
            _Date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("date values must be a real calendar date") from error
        return
    if data_type == "timestamp":
        if not isinstance(value, str) or _TIMESTAMP_TEXT.match(value) is None:
            raise ValueError(
                "timestamp values must be authored as naive date and time text"
            )
        try:
            parsed = _DateTime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                "timestamp values must be a real calendar date and time"
            ) from error
        if parsed.tzinfo is not None:
            raise ValueError("timestamp values must not carry a timezone")
        return
    raise ValueError("unknown scalar type")


class AuthorizationScope(StrictFrozenModel):
    scope_version: Literal["008.scope.v1"]
    tenant_scope_hash: Sha256
    policy_version: VersionTag
    allowed_object_ids: frozenset[SemanticObjectId]
    allowed_classifications: frozenset[Classification]
    authorization_scope_hash: Sha256

    @field_serializer("allowed_object_ids", "allowed_classifications")
    def _serialize_sorted(self, value: frozenset[str]) -> list[str]:
        return _sorted_ids(value)


class ColumnRef(StrictFrozenModel):
    table_id: TableId
    column: ColumnName


class SnapshotColumn(StrictFrozenModel):
    ref: ColumnRef
    data_type: ScalarType
    description: str = ""
    classification: Classification = "internal"


class SnapshotRelationship(StrictFrozenModel):
    relationship_id: RelationshipId
    left: ColumnRef
    right: ColumnRef


class SnapshotWarning(StrictFrozenModel):
    object_id: SemanticObjectId
    warning_hash: Sha256
    kind: Literal["actionable", "informational"]
    control_id: OptionalControlId = ""

    @model_validator(mode="after")
    def _actionability_matches_control_authority(self) -> SnapshotWarning:
        if self.kind == "actionable" and not self.control_id:
            raise ValueError("actionable warnings require a control ID")
        if self.kind == "informational" and self.control_id:
            raise ValueError("informational warnings carry no control authority")
        return self


class SnapshotGovernedLiteral(StrictFrozenModel):
    literal_id: GovernedLiteralId
    data_type: ScalarType
    value: JsonScalar
    source_object_id: SemanticObjectId

    @model_validator(mode="after")
    def _value_matches_declared_type(self) -> SnapshotGovernedLiteral:
        _check_scalar_value(self.data_type, self.value)
        return self


class SnapshotRankingEvidence(StrictFrozenModel):
    object_id: SemanticObjectId
    rank: int = Field(ge=1)
    score: Annotated[float, Field(allow_inf_nan=False)]
    signal_codes: tuple[StableCode, ...] = ()


class SnapshotMetadataObject(StrictFrozenModel):
    object_id: SemanticObjectId
    object_type: Literal[
        "dataset", "table", "concept", "entity", "dimension",
        "business_rule", "relationship", "metric", "policy", "generic"
    ]
    description: str = ""
    columns: tuple[SnapshotColumn, ...] = ()
    formula: str | None = None
    metric_result_type: ScalarType | None = None
    relationships: tuple[SnapshotRelationship, ...] = ()
    warnings: tuple[SnapshotWarning, ...] = ()

    @model_validator(mode="after")
    def _metric_fields_belong_to_metrics(self) -> SnapshotMetadataObject:
        if self.object_type == "metric":
            if self.formula is None:
                raise ValueError("metrics require a governed formula")
            if self.metric_result_type is None:
                raise ValueError("metrics require a declared result type")
            return self
        if self.formula is not None:
            raise ValueError("only metrics may declare a formula")
        if self.metric_result_type is not None:
            raise ValueError("only metrics may declare a result type")
        return self


class DialectCapabilities(StrictFrozenModel):
    parameter_style: Literal["qmark"] = "qmark"
    supports_window: bool
    supports_set_operations: bool


class GroundingSnapshot(StrictFrozenModel):
    snapshot_version: Literal["008.grounding.v1"]
    semantic_version: VersionTag
    policy_version: VersionTag
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    type_registry_version: Literal["008.types.v1"]
    authorization_scope_hash: Sha256
    retrieval_config_hash: Sha256
    dialect: Literal["duckdb"]
    objects: tuple[SnapshotMetadataObject, ...] = ()
    governed_literals: tuple[SnapshotGovernedLiteral, ...] = ()
    ranking_evidence: tuple[SnapshotRankingEvidence, ...] = ()
    authorized_object_ids: frozenset[SemanticObjectId] = frozenset()
    policy_ids: frozenset[PolicyId] = frozenset()
    dialect_capabilities: DialectCapabilities
    snapshot_hash: Sha256

    @field_serializer("authorized_object_ids", "policy_ids")
    def _serialize_sorted(self, value: frozenset[str]) -> list[str]:
        return _sorted_ids(value)


# Value-free literal references. Neither variant carries a resolved or raw
# value: a question literal is an exact code-point span, and a governed literal
# is a snapshot registry identifier.
class QuestionLiteralRef(StrictFrozenModel):
    kind: Literal["question"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    data_type: ScalarType

    @model_validator(mode="after")
    def _span_is_ordered(self) -> QuestionLiteralRef:
        if self.end <= self.start:
            raise ValueError("question literal spans must end after they start")
        return self


class GovernedLiteralRef(StrictFrozenModel):
    kind: Literal["governed"]
    literal_id: GovernedLiteralId


LiteralRef: TypeAlias = Annotated[
    QuestionLiteralRef | GovernedLiteralRef,
    Field(discriminator="kind"),
]


def _expression_depth(expression: Any) -> int:
    """Return expression depth where a leaf is one and each edge adds one."""
    kind = getattr(expression, "kind", None)
    if kind == "function":
        children: tuple[Any, ...] = tuple(expression.arguments)
    elif kind == "binary":
        children = (expression.left, expression.right)
    elif kind == "in":
        children = (expression.expression, *expression.values)
    elif kind == "case":
        children = (
            *(
                item
                for branch in expression.branches
                for item in (branch.when, branch.then)
            ),
            expression.else_expression,
        )
    else:
        return 1
    if not children:
        return 1
    return 1 + max(_expression_depth(child) for child in children)


def _check_expression_depth(expression: Any) -> None:
    if _expression_depth(expression) > MAX_EXPRESSION_DEPTH:
        raise ValueError(f"expression depth must not exceed {MAX_EXPRESSION_DEPTH}")


class ColumnExpression(StrictFrozenModel):
    kind: Literal["column"]
    ref: ColumnRef


class MetricExpression(StrictFrozenModel):
    kind: Literal["metric"]
    metric_id: MetricId


class LiteralExpression(StrictFrozenModel):
    kind: Literal["literal"]
    ref: LiteralRef


class FunctionExpression(StrictFrozenModel):
    kind: Literal["function"]
    function: AllowedFunction
    arguments: tuple[IRExpression, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )

    @model_validator(mode="after")
    def _depth_is_bounded(self) -> FunctionExpression:
        _check_expression_depth(self)
        return self


class BinaryExpression(StrictFrozenModel):
    kind: Literal["binary"]
    operator: AllowedBinaryOperator
    left: IRExpression
    right: IRExpression

    @model_validator(mode="after")
    def _depth_is_bounded(self) -> BinaryExpression:
        _check_expression_depth(self)
        return self


class InExpression(StrictFrozenModel):
    kind: Literal["in"]
    expression: IRExpression
    values: tuple[IRExpression, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    negated: bool = False

    @model_validator(mode="after")
    def _depth_is_bounded(self) -> InExpression:
        _check_expression_depth(self)
        return self


class WhenThen(StrictFrozenModel):
    when: IRExpression
    then: IRExpression


class CaseExpression(StrictFrozenModel):
    kind: Literal["case"]
    branches: tuple[WhenThen, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    else_expression: IRExpression

    @model_validator(mode="after")
    def _depth_is_bounded(self) -> CaseExpression:
        _check_expression_depth(self)
        return self


class RelativeTimeExpression(StrictFrozenModel):
    kind: Literal["relative_time"]
    date_column: ColumnRef
    anchor: Literal["data_max"]
    amount_ref: LiteralRef
    unit: Literal["day", "week", "month", "quarter", "year"]
    lower_inclusive: bool
    upper_inclusive: bool


IRExpression: TypeAlias = Annotated[
    ColumnExpression
    | MetricExpression
    | LiteralExpression
    | FunctionExpression
    | BinaryExpression
    | InExpression
    | CaseExpression
    | RelativeTimeExpression,
    Field(discriminator="kind"),
]


class NamedExpression(StrictFrozenModel):
    alias: ColumnName
    expression: IRExpression


class SortKey(StrictFrozenModel):
    expression: IRExpression
    direction: Literal["asc", "desc"]
    nulls: Literal["first", "last"]


class WindowExpression(StrictFrozenModel):
    alias: ColumnName
    function: Literal["lag", "lead", "row_number", "rank", "dense_rank"]
    argument: IRExpression | None = None
    partition_by: tuple[IRExpression, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    order_by: tuple[SortKey, ...] = Field(default=(), max_length=MAX_EXPRESSION_ITEMS)
    offset: LiteralRef | None = None


class ScanNode(StrictFrozenModel):
    kind: Literal["scan"]
    node_id: NodeId
    table_id: TableId


class JoinNode(StrictFrozenModel):
    kind: Literal["join"]
    node_id: NodeId
    left_id: NodeId
    right_id: NodeId
    relationship_id: RelationshipId
    join_type: Literal["inner", "left"]


class FilterNode(StrictFrozenModel):
    kind: Literal["filter"]
    node_id: NodeId
    input_id: NodeId
    predicate: IRExpression


class AggregateNode(StrictFrozenModel):
    kind: Literal["aggregate"]
    node_id: NodeId
    input_id: NodeId
    group_by: tuple[NamedExpression, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    measures: tuple[NamedExpression, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    minimum_group_size: LiteralRef | None = None


class ProjectNode(StrictFrozenModel):
    kind: Literal["project"]
    node_id: NodeId
    input_id: NodeId
    outputs: tuple[NamedExpression, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


class SortNode(StrictFrozenModel):
    kind: Literal["sort"]
    node_id: NodeId
    input_id: NodeId
    keys: tuple[SortKey, ...] = Field(min_length=1, max_length=MAX_EXPRESSION_ITEMS)


class LimitNode(StrictFrozenModel):
    kind: Literal["limit"]
    node_id: NodeId
    input_id: NodeId
    count: LiteralRef


class WindowNode(StrictFrozenModel):
    kind: Literal["window"]
    node_id: NodeId
    input_id: NodeId
    outputs: tuple[WindowExpression, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


class SetOperationNode(StrictFrozenModel):
    kind: Literal["set_operation"]
    node_id: NodeId
    left_id: NodeId
    right_id: NodeId
    operator: Literal["union", "intersect", "except"]
    all: bool = False


IRNode: TypeAlias = Annotated[
    ScanNode
    | JoinNode
    | FilterNode
    | AggregateNode
    | ProjectNode
    | SortNode
    | LimitNode
    | WindowNode
    | SetOperationNode,
    Field(discriminator="kind"),
]


class WarningRef(StrictFrozenModel):
    object_id: SemanticObjectId
    warning_hash: Sha256


class WarningDecision(StrictFrozenModel):
    warning: WarningRef
    control_id: ControlId
    decision: Literal["applied", "not_applicable"]


def _dump_refs(refs: tuple[Any, ...]) -> list[Any]:
    return [ref.model_dump(mode="json") for ref in refs]


class DirectionMappingAssumption(StrictFrozenModel):
    kind: Literal["direction_mapping"]
    column: ColumnRef
    inflow_refs: tuple[LiteralRef, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    outflow_refs: tuple[LiteralRef, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )

    @model_validator(mode="after")
    def _directions_are_distinct(self) -> DirectionMappingAssumption:
        inflow = _dump_refs(self.inflow_refs)
        outflow = _dump_refs(self.outflow_refs)
        if any(ref in outflow for ref in inflow):
            raise ValueError("inflow and outflow references must be distinct")
        return self


class StatusMappingAssumption(StrictFrozenModel):
    kind: Literal["status_mapping"]
    column: ColumnRef
    semantic_state_id: SemanticStateId
    literal_refs: tuple[LiteralRef, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


class GrainMappingAssumption(StrictFrozenModel):
    kind: Literal["grain_mapping"]
    source_table_ids: tuple[TableId, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    grouping_columns: tuple[ColumnRef, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    physical_operands: tuple[LiteralRef, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )


class SnapshotAssumption(StrictFrozenModel):
    kind: Literal["snapshot"]
    column: ColumnRef
    interpretation_code: StableCode
    physical_operands: tuple[LiteralRef, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )


Assumption: TypeAlias = Annotated[
    DirectionMappingAssumption
    | StatusMappingAssumption
    | GrainMappingAssumption
    | SnapshotAssumption,
    Field(discriminator="kind"),
]


class RequestedDisclosure(StrictFrozenModel):
    source_columns: tuple[ColumnRef, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    limit_ref: LiteralRef


class RelationalQueryIR(StrictFrozenModel):
    outcome: Literal["ir"]
    ir_version: Literal["008.ir.v1"]
    root_node_id: NodeId
    nodes: tuple[IRNode, ...] = Field(min_length=1, max_length=MAX_IR_NODES)
    warning_decisions: tuple[WarningDecision, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    assumptions: tuple[Assumption, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    requested_disclosures: tuple[RequestedDisclosure, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )


class TableGroundingNeed(StrictFrozenModel):
    kind: Literal["table"]
    object_id: TableId


class ColumnGroundingNeed(StrictFrozenModel):
    kind: Literal["column"]
    ref: ColumnRef


class MetricGroundingNeed(StrictFrozenModel):
    kind: Literal["metric"]
    metric_id: MetricId


class RelationshipGroundingNeed(StrictFrozenModel):
    kind: Literal["relationship"]
    relationship_id: RelationshipId


GroundingNeed: TypeAlias = Annotated[
    TableGroundingNeed
    | ColumnGroundingNeed
    | MetricGroundingNeed
    | RelationshipGroundingNeed,
    Field(discriminator="kind"),
]


class CheckViolation(StrictFrozenModel):
    code: StableCode
    stage: PipelineStage
    subject_ids: tuple[StableSubjectId, ...] = ()


class GroundingUsage(StrictFrozenModel):
    object_ids: frozenset[SemanticObjectId] = frozenset()
    relationship_ids: frozenset[RelationshipId] = frozenset()
    governed_literal_ids: frozenset[GovernedLiteralId] = frozenset()

    @field_serializer("object_ids", "relationship_ids", "governed_literal_ids")
    def _serialize_sorted(self, value: frozenset[str]) -> list[str]:
        return _sorted_ids(value)


class AttemptRecord(StrictFrozenModel):
    stage: PipelineStage
    ordinal: int = Field(ge=1)
    outcome: Literal["accepted", "rejected"]
    latency_ms: int = Field(ge=0)
    violation_codes: tuple[StableCode, ...] = ()
    generation_route: ResponseGenerationRoute
    cache_status: CacheStatus


class QueryResult(StrictFrozenModel):
    columns: tuple[ColumnName, ...]
    column_types: tuple[ScalarType, ...]
    rows: tuple[tuple[JsonScalar, ...], ...]
    row_count: int = Field(ge=0)
    truncated: bool
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _shape_is_consistent(self) -> QueryResult:
        if len(self.columns) != len(self.column_types):
            raise ValueError("column types must describe every column")
        if self.row_count != len(self.rows):
            raise ValueError("row count must match the returned rows")
        # Consumers align cells to columns positionally, so a ragged row would
        # misattribute values across columns and across classifications.
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("every row must have one cell per column")
        return self


class OutputLineage(StrictFrozenModel):
    output_name: ColumnName
    source_columns: tuple[ColumnRef, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    metric_ids: tuple[MetricId, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    classification: Classification

    @model_validator(mode="after")
    def _sensitive_classification_needs_provenance(self) -> OutputLineage:
        # A confidential or restricted label with no source column and no metric
        # names nothing: a reader could not tell what was disclosed, so the
        # downstream narrator could not decide what it may repeat.
        if self.classification in {"confidential", "restricted"} and not (
            self.source_columns or self.metric_ids
        ):
            raise ValueError("a sensitive output must name its provenance")
        return self


class DisclosureRecord(StrictFrozenModel):
    output_name: ColumnName
    source_columns: tuple[ColumnRef, ...] = Field(min_length=1)
    classification: Classification
    row_limit: int = Field(ge=0)


def _has_complex_nodes(ir: RelationalQueryIR) -> bool:
    return any(node.kind in SUPPORTED_COMPLEX_NODE_KINDS for node in ir.nodes)


def _check_route_ir_and_plan_hash(
    generation_route: str,
    accepted_complex_plan_hash: str | None,
    ir: RelationalQueryIR,
) -> None:
    if generation_route == "planned_ir":
        if accepted_complex_plan_hash is None:
            raise ValueError("the planned route requires an accepted plan hash")
        return
    if accepted_complex_plan_hash is not None:
        raise ValueError("the default route must not carry an accepted plan hash")
    if _has_complex_nodes(ir):
        raise ValueError("the default route must not contain complex IR nodes")


class ValidatedIR(StrictFrozenModel):
    ir: RelationalQueryIR
    ir_hash: Sha256
    snapshot_hash: Sha256
    canonical_question_hash: Sha256
    generation_route: GenerationRoute
    accepted_complex_plan_hash: Sha256 | None = None

    @model_validator(mode="after")
    def _route_matches_plan_and_nodes(self) -> ValidatedIR:
        _check_route_ir_and_plan_hash(
            self.generation_route, self.accepted_complex_plan_hash, self.ir
        )
        return self


class CachedGeneration(StrictFrozenModel):
    ir: RelationalQueryIR
    generation_route: GenerationRoute
    accepted_complex_plan_hash: Sha256 | None = None
    payload_sha256: Sha256

    @model_validator(mode="after")
    def _route_matches_plan_and_nodes(self) -> CachedGeneration:
        _check_route_ir_and_plan_hash(
            self.generation_route, self.accepted_complex_plan_hash, self.ir
        )
        return self


class ComplexPlanStep(StrictFrozenModel):
    step_id: StableCode
    operator_id: ComplexOperatorId
    depends_on: tuple[StableCode, ...] = Field(default=(), max_length=MAX_PLAN_STEPS)
    input_object_ids: tuple[SemanticObjectId, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )
    output_names: tuple[ColumnName, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


class ComplexQueryPlan(StrictFrozenModel):
    outcome: Literal["complex_plan"]
    plan_version: Literal["008.complex-plan.v1"]
    operator_ids: tuple[ComplexOperatorId, ...] = Field(
        min_length=1, max_length=MAX_PLAN_STEPS
    )
    steps: tuple[ComplexPlanStep, ...] = Field(min_length=1, max_length=MAX_PLAN_STEPS)
    expected_outputs: tuple[ColumnName, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


_SHA256_ADAPTER = TypeAdapter(Sha256)


def _canonical_model_bytes(model: BaseModel) -> bytes:
    """Serialize a validated model with the repository's canonical convention.

    This mirrors `cerebro.provenance.canonical_json_bytes` byte-for-byte. It is
    duplicated rather than imported because `cerebro.provenance` imports this
    module, so importing it back would create a cycle.
    """
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def complex_plan_sha256(plan: ComplexQueryPlan) -> Sha256:
    """Return the single authoritative digest of an accepted complex plan."""
    if not isinstance(plan, ComplexQueryPlan):
        raise TypeError("a plan digest requires a validated complex plan")
    return hashlib.sha256(_canonical_model_bytes(plan)).hexdigest()


def relational_ir_sha256(ir: RelationalQueryIR) -> Sha256:
    """Return the single authoritative digest of a value-free relational IR."""
    if not isinstance(ir, RelationalQueryIR):
        raise TypeError("an IR digest requires a validated RelationalQueryIR")
    return hashlib.sha256(_canonical_model_bytes(ir)).hexdigest()


def _refuse_state_protocol(name: str) -> Any:
    """Return state-protocol methods that fail closed for local capabilities.

    `dataclasses` generates `__getstate__`/`__setstate__` for frozen slotted
    classes, and `__setstate__` assigns through `object.__setattr__`. Left open,
    that mints a forged capability, rewrites a frozen one in place, skips
    `__post_init__`, and gives `dataclasses.asdict` a dict path. Copy, pickle,
    and dict conversion therefore all fail closed here.
    """

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise TypeError(f"{name} does not support copy, pickle, or state transfer")

    return _raise


# Module-private minting authority for the planned route. Callers cannot forge
# the capability: the token never leaves this module and the dataclass refuses
# direct construction, including `dataclasses.replace`, copy, and pickle.
_ACCEPTED_COMPLEX_ROUTE_TOKEN = object()


@dataclasses.dataclass(frozen=True, slots=True, init=False)
class AcceptedComplexRoute:
    generation_route: Literal["planned_ir"]
    snapshot_hash: str
    plan_hash: str
    plan: ComplexQueryPlan
    _router_token: object

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("AcceptedComplexRoute is minted by the router, not constructed")

    __getstate__ = _refuse_state_protocol("AcceptedComplexRoute")
    __setstate__ = _refuse_state_protocol("AcceptedComplexRoute")
    __reduce__ = _refuse_state_protocol("AcceptedComplexRoute")
    __reduce_ex__ = _refuse_state_protocol("AcceptedComplexRoute")
    __copy__ = _refuse_state_protocol("AcceptedComplexRoute")
    __deepcopy__ = _refuse_state_protocol("AcceptedComplexRoute")


def _create_accepted_complex_route(
    *,
    snapshot_hash: str,
    plan_hash: str,
    plan: ComplexQueryPlan,
) -> AcceptedComplexRoute:
    if not isinstance(plan, ComplexQueryPlan):
        raise TypeError("an accepted route requires a validated complex plan")
    _SHA256_ADAPTER.validate_python(snapshot_hash)
    _SHA256_ADAPTER.validate_python(plan_hash)
    if plan_hash != complex_plan_sha256(plan):
        raise ValueError("plan hash must be derived from the accepted plan")
    route = object.__new__(AcceptedComplexRoute)
    object.__setattr__(route, "generation_route", "planned_ir")
    object.__setattr__(route, "snapshot_hash", snapshot_hash)
    object.__setattr__(route, "plan_hash", plan_hash)
    object.__setattr__(route, "plan", plan)
    object.__setattr__(route, "_router_token", _ACCEPTED_COMPLEX_ROUTE_TOKEN)
    return route


def _is_authentic_accepted_route(candidate: Any) -> bool:
    if not isinstance(candidate, AcceptedComplexRoute):
        return False
    token = getattr(candidate, "_router_token", None)
    return token is _ACCEPTED_COMPLEX_ROUTE_TOKEN


def _snapshot_is_empty(snapshot: GroundingSnapshot) -> bool:
    return not (
        snapshot.objects
        or snapshot.governed_literals
        or snapshot.ranking_evidence
        or snapshot.authorized_object_ids
        or snapshot.policy_ids
    )


GuardedGenerationMode: TypeAlias = Literal["default_ir", "planned_ir", "provider_probe"]


@dataclasses.dataclass(frozen=True, slots=True)
class GuardedGenerationRequest:
    mode: GuardedGenerationMode
    canonical_question: str
    snapshot: GroundingSnapshot
    accepted_complex_route: AcceptedComplexRoute | None = None
    prior_violations: tuple[CheckViolation, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ("default_ir", "planned_ir", "provider_probe"):
            raise ValueError("unknown guarded generation mode")
        if not isinstance(self.canonical_question, str):
            raise TypeError("a guarded request carries canonical question text")
        if not isinstance(self.snapshot, GroundingSnapshot):
            raise TypeError("a guarded request carries an immutable snapshot")
        if not isinstance(self.prior_violations, tuple) or not all(
            isinstance(item, CheckViolation) for item in self.prior_violations
        ):
            raise TypeError("prior violations must be typed check violations")
        if self.mode == "planned_ir":
            if not _is_authentic_accepted_route(self.accepted_complex_route):
                raise TypeError("the planned mode requires an authentic accepted route")
            route = self.accepted_complex_route
            if route.snapshot_hash != self.snapshot.snapshot_hash:
                raise ValueError("the accepted route must bind to this snapshot")
            if route.plan_hash != complex_plan_sha256(route.plan):
                raise ValueError("the accepted route must bind to its plan hash")
            return
        if self.accepted_complex_route is not None:
            raise ValueError("only the planned mode may carry an accepted route")
        if self.mode == "provider_probe":
            if not _snapshot_is_empty(self.snapshot):
                raise ValueError("a provider probe requires an empty snapshot")
            if self.prior_violations:
                raise ValueError("a provider probe carries no prior violations")

    # Without these, `__setstate__` would skip `__post_init__` entirely and
    # `dataclasses.asdict` would expose the canonical question through a dict.
    __getstate__ = _refuse_state_protocol("GuardedGenerationRequest")
    __setstate__ = _refuse_state_protocol("GuardedGenerationRequest")
    __reduce__ = _refuse_state_protocol("GuardedGenerationRequest")
    __reduce_ex__ = _refuse_state_protocol("GuardedGenerationRequest")
    __copy__ = _refuse_state_protocol("GuardedGenerationRequest")
    __deepcopy__ = _refuse_state_protocol("GuardedGenerationRequest")


class ObjectAmbiguityCandidate(StrictFrozenModel):
    kind: Literal["object"]
    object_id: SemanticObjectId


class RelationshipAmbiguityCandidate(StrictFrozenModel):
    kind: Literal["relationship"]
    relationship_id: RelationshipId


class GovernedLiteralAmbiguityCandidate(StrictFrozenModel):
    kind: Literal["governed_literal"]
    literal_id: GovernedLiteralId


class GrainAmbiguityCandidate(StrictFrozenModel):
    kind: Literal["grain"]
    grain: Literal["row", "day", "week", "month", "quarter", "year"]
    grouping_columns: tuple[ColumnRef, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


class OperatorAmbiguityCandidate(StrictFrozenModel):
    kind: Literal["operator"]
    operator_id: ComplexOperatorId


AmbiguityCandidate: TypeAlias = Annotated[
    ObjectAmbiguityCandidate
    | RelationshipAmbiguityCandidate
    | GovernedLiteralAmbiguityCandidate
    | GrainAmbiguityCandidate
    | OperatorAmbiguityCandidate,
    Field(discriminator="kind"),
]


class Ambiguity(StrictFrozenModel):
    ambiguity_id: StableCode
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    candidates: tuple[AmbiguityCandidate, ...] = Field(
        min_length=2, max_length=MAX_EXPRESSION_ITEMS
    )

    @model_validator(mode="after")
    def _span_and_candidates_are_well_formed(self) -> Ambiguity:
        if self.end <= self.start:
            raise ValueError("ambiguity spans must end after they start")
        kinds = {candidate.kind for candidate in self.candidates}
        if len(kinds) != 1:
            raise ValueError("ambiguity candidates must share one kind")
        dumped = [candidate.model_dump(mode="json") for candidate in self.candidates]
        for index, candidate in enumerate(dumped):
            if candidate in dumped[index + 1 :]:
                raise ValueError("ambiguity candidates must be distinct")
        return self


class ClarificationRequest(StrictFrozenModel):
    outcome: Literal["clarification_request"]
    ambiguities: tuple[Ambiguity, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


class LiteralClarificationNeed(StrictFrozenModel):
    kind: Literal["literal_need"]
    issue: Literal[
        "missing_literal",
        "invalid_question_span",
        "unparseable_question_literal",
        "ungrounded_governed_literal",
        "literal_type_mismatch",
        "invented_literal_reference",
    ]
    expected_type: ScalarType
    target_column: ColumnRef | None = None
    literal_ref: LiteralRef | None = None


class GroundingRefusal(StrictFrozenModel):
    outcome: Literal["grounding_refusal"]
    unmet_needs: tuple[GroundingNeed, ...] = Field(
        min_length=1, max_length=MAX_EXPRESSION_ITEMS
    )


IRGenerationOutcome: TypeAlias = Annotated[
    RelationalQueryIR | ComplexQueryPlan | GroundingRefusal | ClarificationRequest,
    Field(discriminator="outcome"),
]


class BoundParameter(StrictFrozenModel):
    position: int = Field(ge=1)
    data_type: ScalarType
    value: JsonScalar = Field(exclude=True, repr=False)

    def __str__(self) -> str:
        # `exclude` only stops serialization. A resolved literal must also stay
        # out of reprs, logs, tracebacks, and assertion output, because those
        # are exactly where a value escapes without anyone choosing to dump it.
        return self.__repr__()


class CompiledQuery(StrictFrozenModel):
    sql: str = Field(min_length=1)
    parameters: tuple[BoundParameter, ...] = ()
    ir_hash: Sha256
    compiler_version: VersionTag
    dialect: Literal["duckdb"]


class SQLArtifact(StrictFrozenModel):
    sql: str = Field(min_length=1)
    sql_sha256: Sha256
    parameter_count: int = Field(ge=0)
    parameter_types: tuple[ScalarType, ...] = ()
    ir_hash: Sha256
    compiler_version: VersionTag
    dialect: Literal["duckdb"]

    @model_validator(mode="after")
    def _count_matches_types(self) -> SQLArtifact:
        if self.parameter_count != len(self.parameter_types):
            raise ValueError("parameter count must match the declared types")
        return self


class BudgetLimits(StrictFrozenModel):
    # Frozen per the spec: a cap any holder can raise is not a cap.
    initial_semantic_call_capacity: Literal[1]
    planned_semantic_call_capacity: Literal[2]
    max_transport_attempts_per_semantic_call: int = Field(ge=1, le=2)
    provider_timeout_ms_per_attempt: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_cost_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    end_to_end_deadline_ms: int = Field(gt=0)

    @classmethod
    def defaults(cls) -> BudgetLimits:
        return cls(
            initial_semantic_call_capacity=1,
            planned_semantic_call_capacity=2,
            max_transport_attempts_per_semantic_call=2,
            provider_timeout_ms_per_attempt=20_000,
            max_input_tokens=32_000,
            max_output_tokens=8_000,
            max_cost_usd=Decimal("0.50"),
            end_to_end_deadline_ms=120_000,
        )


class BudgetUsage(StrictModel):
    # Accounting mutates usage, so bounds must be revalidated on assignment.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    semantic_call_capacity: Literal[1, 2]
    planned_ir_authorized: bool
    semantic_calls: int = Field(ge=0)
    transport_attempts: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    elapsed_ms: int = Field(ge=0)


class SQLGenerationRequest(StrictModel):
    question: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
    authorization_scope: AuthorizationScope
    dialect: Literal["duckdb"] = "duckdb"
    max_rows: int = Field(default=1000, ge=1, le=10_000)


class ResponseBase(StrictModel):
    # `validate_assignment` keeps route/evidence coherence from being edited
    # away after validation; `protected_namespaces` allows `model_revision`.
    model_config = ConfigDict(
        extra="forbid", protected_namespaces=(), validate_assignment=True
    )

    contract_version: Literal["008.v3"]
    semantic_version: VersionTag
    policy_version: VersionTag
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    ir_contract_version: Literal["008.ir.v1"]
    type_registry_version: Literal["008.types.v1"]
    prompt_version: VersionTag
    router_version: VersionTag
    compiler_version: VersionTag
    checker_version: VersionTag
    dialect: Literal["duckdb"]
    provider: VersionTag
    model: VersionTag
    model_revision: VersionTag
    canonical_question_hash: Sha256
    authorization_scope_hash: Sha256
    snapshot_hash: Sha256 | None = None
    generation_route: ResponseGenerationRoute
    cache_status: CacheStatus
    grounding_usage: GroundingUsage
    assumptions: tuple[Assumption, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )
    attempt_records: tuple[AttemptRecord, ...] = Field(
        default=(), max_length=MAX_ATTEMPT_RECORDS
    )
    budget_usage: BudgetUsage
    violations: tuple[CheckViolation, ...] = Field(
        default=(), max_length=MAX_EXPRESSION_ITEMS
    )

    @model_validator(mode="after")
    def _route_evidence_is_coherent(self) -> ResponseBase:
        usage = self.budget_usage
        planned_attempts = tuple(
            record
            for record in self.attempt_records
            if record.stage == "planned_ir" or record.generation_route == "planned_ir"
        )
        if self.generation_route == "none":
            if self.snapshot_hash is not None:
                raise ValueError("a pre-generation response has no snapshot hash")
            if self.attempt_records:
                raise ValueError("a pre-generation response records no attempts")
            if usage.semantic_calls or usage.transport_attempts:
                raise ValueError("a pre-generation response spends no semantic calls")
            if usage.planned_ir_authorized:
                raise ValueError("a pre-generation response authorizes no plan")
            if (
                self.grounding_usage.object_ids
                or self.grounding_usage.relationship_ids
                or self.grounding_usage.governed_literal_ids
            ):
                raise ValueError("a pre-generation response uses no grounding")
            return self
        if self.snapshot_hash is None:
            raise ValueError("a generated response must cite its snapshot hash")
        # A validated cache hit preserves its original route with zero semantic
        # calls, so planned-route evidence is required only when a call happened.
        served_from_cache = self.cache_status == "hit" and usage.semantic_calls == 0
        if self.generation_route == "planned_ir":
            if served_from_cache:
                return self
            if not usage.planned_ir_authorized:
                raise ValueError("the planned route requires planned authorization")
            if usage.semantic_call_capacity != 2:
                raise ValueError("the planned route requires planned capacity")
            if not planned_attempts:
                raise ValueError("the planned route requires a planned attempt")
            return self
        if served_from_cache:
            return self
        if usage.planned_ir_authorized:
            raise ValueError("the default route authorizes no plan")
        if usage.semantic_call_capacity != 1:
            raise ValueError("the default route keeps the initial capacity")
        if planned_attempts:
            raise ValueError("the default route records no planned attempt")
        return self


class OkResponse(ResponseBase):
    status: Literal["ok"]
    generation_route: GenerationRoute
    snapshot_hash: Sha256
    ir: RelationalQueryIR
    sql_artifact: SQLArtifact
    result: QueryResult
    output_lineage: tuple[OutputLineage, ...]
    disclosures: tuple[DisclosureRecord, ...]

    @model_validator(mode="after")
    def _ir_matches_generation_route(self) -> OkResponse:
        if self.generation_route == "default_ir" and _has_complex_nodes(self.ir):
            raise ValueError("the default route must not contain complex IR nodes")
        return self


class CheckFailedResponse(ResponseBase):
    status: Literal["check_failed"]
    executable: Literal[False] = False
    ir: RelationalQueryIR | None = None
    sql_artifact: SQLArtifact | None = None
    violations: tuple[CheckViolation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _route_matches_generation_evidence(self) -> CheckFailedResponse:
        if self.generation_route == "none" and (
            self.ir is not None or self.sql_artifact is not None
        ):
            raise ValueError("a pre-generation failure carries no generated evidence")
        if (
            self.generation_route == "default_ir"
            and self.ir is not None
            and _has_complex_nodes(self.ir)
        ):
            raise ValueError("the default route must not contain complex IR nodes")
        return self


_REFUSAL_POPULATION_NAMES = (
    "unmet_needs",
    "policy_ids",
    "unsupported_operator_ids",
    "ambiguities",
    "literal_needs",
)
_REFUSAL_POPULATION_MATRIX: dict[str, frozenset[frozenset[str]]] = {
    "missing_grounding": frozenset({frozenset({"unmet_needs"})}),
    "policy_disallowed": frozenset({frozenset({"policy_ids"})}),
    "unsupported_complexity": frozenset({frozenset({"unsupported_operator_ids"})}),
    "clarification_required": frozenset(
        {frozenset({"ambiguities"}), frozenset({"literal_needs"})}
    ),
}


class RefusedResponse(ResponseBase):
    status: Literal["refused"]
    reason: Literal[
        "missing_grounding",
        "policy_disallowed",
        "clarification_required",
        "unsupported_complexity",
    ]
    unmet_needs: tuple[GroundingNeed, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    literal_needs: tuple[LiteralClarificationNeed, ...] = ()
    policy_ids: tuple[PolicyId, ...] = ()
    unsupported_operator_ids: tuple[ComplexOperatorId, ...] = ()

    @model_validator(mode="after")
    def _reason_matches_typed_populations(self) -> RefusedResponse:
        if self.generation_route == "none":
            raise ValueError("a refusal reports the route it reached")
        populated = frozenset(
            name for name in _REFUSAL_POPULATION_NAMES if getattr(self, name)
        )
        if populated not in _REFUSAL_POPULATION_MATRIX[self.reason]:
            raise ValueError("refusal populations must match the refusal reason")
        if self.ambiguities and self.generation_route != "default_ir":
            raise ValueError("model ambiguity is recorded on the default route")
        return self


SQLGenerationResponse: TypeAlias = Annotated[
    OkResponse | CheckFailedResponse | RefusedResponse,
    Field(discriminator="status"),
]


# Rebuild the recursive IR contracts only after every variant is declared, so
# the discriminated unions resolve against complete definitions.
for _model in (
    FunctionExpression,
    BinaryExpression,
    InExpression,
    WhenThen,
    CaseExpression,
    NamedExpression,
    SortKey,
    WindowExpression,
    FilterNode,
    AggregateNode,
    ProjectNode,
    SortNode,
    LimitNode,
    WindowNode,
    RelationalQueryIR,
    ValidatedIR,
    CachedGeneration,
    OkResponse,
    CheckFailedResponse,
    RefusedResponse,
):
    _model.model_rebuild()
del _model


# ---------------------------------------------------------------------------
# Live Option B baseline evidence.
#
# A baseline is only worth publishing if a reader can reconstruct exactly which
# code, data, policy, and model produced it. Every field below is mandatory for
# that reason: an artifact that omits one cannot be audited later, and an
# optional provenance field is provenance nobody can rely on.
# ---------------------------------------------------------------------------

EVALUATION_ARTIFACT_VERSION = "008.artifact.v3"

BaselineBlockerCode: TypeAlias = Literal[
    "evidence_drift",
    "stale_capability_probe",
    "provider_identity_mismatch",
    "model_identity_mismatch",
    "model_revision_mismatch",
    "schema_mechanism_mismatch",
    "dirty_code_revision",
    "unknown_code_revision",
    "question_cardinality_mismatch",
    "duplicate_question_id",
    "unexpected_question_id",
    "falsified_totals",
    "all_questions_failed",
    "generation_route_mismatch",
    "cache_status_mismatch",
    "semantic_call_count_mismatch",
    "cached_route_not_revalidated",
    "unauthorized_budget_transition",
    "repeated_budget_transition",
    "budget_overflow",
    "canonicalization_version_drift",
    "literal_registry_version_drift",
    "type_registry_version_drift",
    "serialized_canonical_question",
    "serialized_resolved_value",
    "missing_v3_provenance",
    "missing_live_provider",
]


class ArtifactProvenance(_StrictFrozenEvidenceModel):
    """Everything needed to reproduce one live baseline run."""

    contract_version: Literal["008.artifact.v3"]
    run_id: EvidenceIdentifier
    dialect: Literal["duckdb"]

    # Retrieval, authorization, and policy.
    retrieval_config_sha256: Sha256Digest
    policy_version: EvidenceIdentifier
    semantic_version: EvidenceIdentifier
    authorization_scope_hash: Sha256Digest

    # Question and literal identity.
    canonicalization_version: EvidenceIdentifier
    canonical_question_hash_algorithm: Literal["sha256"]
    literal_registry_version: EvidenceIdentifier

    # Generation and checking.
    prompt_version: EvidenceIdentifier
    ir_contract_version: EvidenceIdentifier
    type_registry_version: EvidenceIdentifier
    router_version: EvidenceIdentifier
    compiler_version: EvidenceIdentifier
    checker_sha256: Sha256Digest
    cache_identity_version: EvidenceIdentifier

    # Exact effective limits, plus the hash that pins them.
    budget_limits: BudgetLimits
    budget_limits_sha256: Sha256Digest

    # The organizer runtime that actually answered.
    provider: EvidenceIdentifier
    model: EvidenceIdentifier
    model_revision: EvidenceIdentifier
    schema_mechanism: EvidenceIdentifier
    capability_receipt_sha256: Sha256Digest

    # The authoritative data this run read.
    source_manifest_sha256: Sha256Digest
    materialization_receipt_sha256: Sha256Digest
    bundle_sha256: Sha256Digest
    database_sha256: Sha256Digest
    golden_set_sha256: Sha256Digest

    # The exact code revision.
    code_revision: EvidenceIdentifier
    code_dirty: bool


class EvaluationQuestion(_StrictFrozenEvidenceModel):
    """One baseline question's terminal evidence. It carries no values."""

    question_id: EvidenceIdentifier
    canonical_question_hash: Sha256Digest
    authorization_scope_hash: Sha256Digest
    snapshot_hash: Sha256Digest
    status: Literal["ok", "refused", "check_failed"]
    generation_route: Literal["default_ir", "planned_ir"]
    cache_status: Literal["miss", "hit"]
    ir_hash: Sha256Digest | None = None
    sql_artifact: SQLArtifact | None = None
    attempt_records: tuple[AttemptRecord, ...] = Field(min_length=1)
    budget_usage: BudgetUsage
    result_columns: tuple[ColumnName, ...] = ()
    result_column_types: tuple[ScalarType, ...] = ()
    row_count: int | None = None
    output_lineage: tuple[OutputLineage, ...] = ()
    disclosures: tuple[DisclosureRecord, ...] = ()
    violation_codes: tuple[StableCode, ...] = ()
    refusal_reason: str | None = None

    @model_validator(mode="after")
    def _evidence_is_internally_consistent(self) -> EvaluationQuestion:
        # Semantic call totals are derived from the attempts, never reported
        # independently: a self-declared total could not be cross-checked.
        # A generation stage labels both the provider call and the local IR
        # validation that follows it, and it is also recorded when a cached IR is
        # revalidated. Each route is entered at most once per request, so the
        # number of distinct generation stages reached while the cache missed is
        # exactly the number of semantic calls.
        derived = len(
            {
                record.stage
                for record in self.attempt_records
                if record.stage in {"default_ir", "planned_ir"}
                and record.outcome == "accepted"
                and record.cache_status == "miss"
            }
        )
        if derived != self.budget_usage.semantic_calls:
            raise ValueError("semantic calls must be derived from the attempts")
        if len(self.result_columns) != len(self.result_column_types):
            raise ValueError("result column types must describe every column")
        if self.status == "ok":
            if self.sql_artifact is None or self.ir_hash is None:
                raise ValueError("an ok question reports its IR hash and artifact")
            if self.row_count is None:
                raise ValueError("an ok question reports its row count")
            if not self.output_lineage:
                raise ValueError("an ok question reports output lineage")
            if self.violation_codes:
                raise ValueError("an ok question reports no violations")
        if self.status == "refused" and not self.refusal_reason:
            raise ValueError("a refusal reports its typed reason")
        if self.status == "check_failed" and not self.violation_codes:
            raise ValueError("a check failure reports at least one violation code")
        if self.cache_status == "hit" and self.budget_usage.semantic_calls != 0:
            raise ValueError("a cache hit consumes no semantic call")
        planned_miss = (
            self.generation_route == "planned_ir" and self.cache_status == "miss"
        )
        if planned_miss and not self.budget_usage.planned_ir_authorized:
            raise ValueError("a planned miss requires an authorized transition")
        default_miss = (
            self.generation_route == "default_ir" and self.cache_status == "miss"
        )
        if default_miss and self.budget_usage.planned_ir_authorized:
            raise ValueError("a default miss authorizes no transition")
        return self


class EvaluationArtifact(_StrictFrozenEvidenceModel):
    """The only artifact a live baseline may publish."""

    run_kind: Literal["live_unadapted_baseline"]
    artifact_version: Literal["008.artifact.v3"]
    provenance: ArtifactProvenance
    questions: tuple[EvaluationQuestion, ...] = Field(min_length=1)
    total_questions: int = Field(ge=1)
    ok_count: int = Field(ge=0)
    refused_count: int = Field(ge=0)
    check_failed_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _totals_are_derived_and_non_vacuous(self) -> EvaluationArtifact:
        if self.total_questions != len(self.questions):
            raise ValueError("the reported total must equal the question count")
        identifiers = [item.question_id for item in self.questions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("every question id must be unique")
        counted = {
            "ok": self.ok_count,
            "refused": self.refused_count,
            "check_failed": self.check_failed_count,
        }
        for status, reported in counted.items():
            derived = sum(1 for item in self.questions if item.status == status)
            if reported != derived:
                raise ValueError(f"the {status} count must be derived from entries")
        if sum(counted.values()) != self.total_questions:
            raise ValueError("status counts must partition every question")
        # A run where nothing succeeded is not a baseline: it measures nothing.
        if self.ok_count < 1:
            raise ValueError("a baseline requires at least one ok question")
        return self


class BlockedEvaluation(_StrictFrozenEvidenceModel):
    """The recorded outcome when evidence is absent or has drifted."""

    run_kind: Literal["blocked"]
    artifact_version: Literal["008.artifact.v3"]
    run_id: EvidenceIdentifier
    blockers: tuple[BaselineBlockerCode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _blockers_are_unique(self) -> BlockedEvaluation:
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        return self
