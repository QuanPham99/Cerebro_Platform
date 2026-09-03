from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


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


class PreflightBlocker(_StrictFrozenEvidenceModel):
    code: PreflightBlockerCode
    gate: PreflightGate


class PreflightReport(_StrictFrozenEvidenceModel):
    offline_ready: bool
    data_prerequisites_ready: bool
    organizer_prerequisites_ready: bool
    live_prerequisites_ready: bool
    blockers: tuple[PreflightBlocker, ...] = ()

    @model_validator(mode="after")
    def readiness_matches_blockers(self) -> PreflightReport:
        data_ready = not any(blocker.gate == "data" for blocker in self.blockers)
        organizer_ready = not any(blocker.gate == "organizer" for blocker in self.blockers)
        if self.data_prerequisites_ready != data_ready:
            raise ValueError("data readiness must match data blockers")
        if self.organizer_prerequisites_ready != organizer_ready:
            raise ValueError("organizer readiness must match organizer blockers")
        if self.live_prerequisites_ready != (data_ready and organizer_ready):
            raise ValueError("live readiness must require data and organizer readiness")
        if len(self.blockers) != len({(item.code, item.gate) for item in self.blockers}):
            raise ValueError("preflight blockers must be unique")
        return self
