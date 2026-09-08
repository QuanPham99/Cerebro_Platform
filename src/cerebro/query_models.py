"""Spec 008 v3 immutable query contracts; Phase 1 advisory models stay separate."""
from __future__ import annotations
import re
import unicodedata
from decimal import Decimal
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NodeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ColumnName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
TableId = Annotated[str, Field(pattern=r"^table\.[a-z][a-z0-9_]*$")]
MetricId = Annotated[str, Field(pattern=r"^metric\.[a-z][a-z0-9_-]*$")]
RelationshipId = Annotated[str, Field(pattern=r"^relationship\.[a-z][a-z0-9_]*$")]
PolicyId = Annotated[str, Field(pattern=r"^policy\.[a-z][a-z0-9_-]*$")]
SemanticObjectId = Annotated[
    str,
    Field(pattern=r"^(dataset|table|concept|relationship|metric|policy)\.[a-z][a-z0-9_-]*$"),
]
Classification = Literal["public", "internal", "confidential", "restricted"]
ScalarType = Literal["string", "integer", "decimal", "boolean", "date", "timestamp"]
JsonScalar = str | int | float | bool | Decimal | None  # internal/snapshot use only; never stored in IR
GenerationRoute = Literal["default_ir", "planned_ir"]
GovernedLiteralId = Annotated[
    str,
    Field(pattern=r"^literal\.[a-z][a-z0-9_-]*$"),
]
AllowedFunction = Literal[
    "count", "sum", "avg", "min", "max", "stddev", "variance",
    "date_trunc", "nullif", "coalesce",
]
AllowedBinaryOperator = Literal[
    "eq", "neq", "lt", "lte", "gt", "gte", "and", "or",
    "add", "subtract", "multiply", "divide",
]
ComplexOperatorId = Literal[
    "window.period_over_period.v1", "set_operation.safe_binary.v1",
]
QUESTION_CANONICALIZATION_VERSION = "008.question.v1"
LITERAL_SPAN_REGISTRY_VERSION = "008.literal-span.v1"
EXPRESSION_TYPE_REGISTRY_VERSION = "008.types.v1"


def canonicalize_question(question: str) -> str:
    normalized = unicodedata.normalize("NFC", question)
    return re.sub(r"\s+", " ", normalized.strip(), flags=re.UNICODE)


class AuthorizationScope(StrictFrozenModel):
    scope_version: Literal["008.scope.v1"]
    tenant_scope_hash: Sha256
    policy_version: str
    allowed_object_ids: frozenset[SemanticObjectId]
    allowed_classifications: frozenset[Classification]
    authorization_scope_hash: Sha256


class SnapshotColumn(StrictFrozenModel):
    ref: "ColumnRef"
    data_type: ScalarType
    description: str
    classification: Classification


class SnapshotRelationship(StrictFrozenModel):
    relationship_id: RelationshipId
    left: "ColumnRef"
    right: "ColumnRef"


class SnapshotWarning(StrictFrozenModel):
    object_id: SemanticObjectId
    warning_hash: Sha256
    kind: Literal["actionable", "informational"]
    control_id: str


class SnapshotGovernedLiteral(StrictFrozenModel):
    literal_id: GovernedLiteralId
    data_type: ScalarType
    value: JsonScalar  # authored semantic constant, never sampled/discovered data
    source_object_id: SemanticObjectId


class SnapshotMetadataObject(StrictFrozenModel):
    object_id: SemanticObjectId
    object_type: Literal["dataset", "table", "concept", "relationship", "metric", "policy"]
    description: str
    dependencies: tuple[SemanticObjectId, ...] = ()
    columns: tuple[SnapshotColumn, ...] = ()
    formula: str | None = None
    metric_result_type: ScalarType | None = None
    relationships: tuple[SnapshotRelationship, ...] = ()
    warnings: tuple[SnapshotWarning, ...] = ()


class GroundingSnapshot(StrictFrozenModel):
    snapshot_version: Literal["008.grounding.v1"]
    semantic_version: str
    policy_version: str
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    type_registry_version: Literal["008.types.v1"]
    authorization_scope_hash: Sha256
    retrieval_config_hash: Sha256
    dialect: Literal["duckdb"]
    objects: tuple[SnapshotMetadataObject, ...]
    governed_literals: tuple[SnapshotGovernedLiteral, ...] = ()
    ranking_evidence: tuple[SnapshotRanking, ...]
    snapshot_hash: Sha256


class ColumnRef(StrictModel):
    table_id: TableId
    column: ColumnName


class ColumnExpression(StrictModel):
    kind: Literal["column"] = "column"
    ref: ColumnRef


class OutputExpression(StrictModel):
    kind: Literal["output"] = "output"
    node_id: NodeId
    alias: ColumnName


class MetricExpression(StrictModel):
    kind: Literal["metric"] = "metric"
    metric_id: MetricId


class QuestionLiteralRef(StrictModel):
    kind: Literal["question"] = "question"
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    data_type: ScalarType


class GovernedLiteralRef(StrictModel):
    kind: Literal["governed"] = "governed"
    literal_id: GovernedLiteralId


LiteralRef = Annotated[
    QuestionLiteralRef | GovernedLiteralRef,
    Field(discriminator="kind"),
]


class LiteralExpression(StrictModel):
    kind: Literal["literal"] = "literal"
    ref: LiteralRef


class FunctionExpression(StrictModel):
    kind: Literal["function"] = "function"
    function: AllowedFunction
    arguments: tuple["IRExpression", ...]


class BinaryExpression(StrictModel):
    kind: Literal["binary"] = "binary"
    operator: AllowedBinaryOperator
    left: "IRExpression"
    right: "IRExpression"


class InExpression(StrictModel):
    kind: Literal["in"] = "in"
    expression: "IRExpression"
    values: tuple[LiteralExpression, ...] = Field(min_length=1, max_length=100)
    negated: bool = False


class WhenThen(StrictModel):
    when: "IRExpression"
    then: "IRExpression"


class CaseExpression(StrictModel):
    kind: Literal["case"] = "case"
    branches: tuple[WhenThen, ...] = Field(min_length=1)
    else_expression: "IRExpression"


class RelativeTimeExpression(StrictModel):
    kind: Literal["relative_time"] = "relative_time"
    date_column: ColumnRef
    anchor: Literal["data_max"]
    amount_ref: LiteralRef
    unit: Literal["day", "week", "month", "quarter", "year"]
    lower_inclusive: bool
    upper_inclusive: bool


IRExpression = Annotated[
    ColumnExpression | OutputExpression | MetricExpression | LiteralExpression |
    FunctionExpression | BinaryExpression | InExpression | CaseExpression |
    RelativeTimeExpression,
    Field(discriminator="kind"),
]


class NamedExpression(StrictModel):
    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    expression: IRExpression


class SortKey(StrictModel):
    expression: IRExpression
    direction: Literal["asc", "desc"]
    nulls: Literal["first", "last"]


class WindowExpression(StrictModel):
    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    function: Literal["lag", "lead", "row_number", "rank", "dense_rank"]
    argument: IRExpression | None = None
    partition_by: tuple[IRExpression, ...] = ()
    order_by: tuple[SortKey, ...] = ()
    offset: LiteralRef | None = None


class ScanNode(StrictModel):
    kind: Literal["scan"] = "scan"
    node_id: NodeId
    table_id: TableId


class JoinNode(StrictModel):
    kind: Literal["join"] = "join"
    node_id: NodeId
    left_id: NodeId
    right_id: NodeId
    relationship_id: RelationshipId
    join_type: Literal["inner", "left"]


class FilterNode(StrictModel):
    kind: Literal["filter"] = "filter"
    node_id: NodeId
    input_id: NodeId
    predicate: IRExpression


class AggregateNode(StrictModel):
    kind: Literal["aggregate"] = "aggregate"
    node_id: NodeId
    input_id: NodeId
    group_by: tuple[NamedExpression, ...]
    measures: tuple[NamedExpression, ...]
    minimum_group_size: LiteralRef | None = None


class ProjectNode(StrictModel):
    kind: Literal["project"] = "project"
    node_id: NodeId
    input_id: NodeId
    outputs: tuple[NamedExpression, ...]


class SortNode(StrictModel):
    kind: Literal["sort"] = "sort"
    node_id: NodeId
    input_id: NodeId
    keys: tuple[SortKey, ...]


class LimitNode(StrictModel):
    kind: Literal["limit"] = "limit"
    node_id: NodeId
    input_id: NodeId
    count: LiteralRef


class WindowNode(StrictModel):
    kind: Literal["window"] = "window"
    node_id: NodeId
    input_id: NodeId
    outputs: tuple[WindowExpression, ...]


class SetOperationNode(StrictModel):
    kind: Literal["set_operation"] = "set_operation"
    node_id: NodeId
    left_id: NodeId
    right_id: NodeId
    operator: Literal["union", "intersect", "except"]
    all: bool = False


IRNode = Annotated[
    ScanNode | JoinNode | FilterNode | AggregateNode | ProjectNode |
    SortNode | LimitNode | WindowNode | SetOperationNode,
    Field(discriminator="kind"),
]


class RelationalQueryIR(StrictModel):
    outcome: Literal["ir"] = "ir"
    ir_version: Literal["008.ir.v1"]
    root_node_id: NodeId
    nodes: tuple[IRNode, ...]
    warning_decisions: tuple[WarningDecision, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    requested_disclosures: tuple[RequestedDisclosure, ...] = ()


class ValidatedIR(StrictFrozenModel):
    ir: RelationalQueryIR
    ir_hash: Sha256
    snapshot_hash: Sha256
    canonical_question_hash: Sha256
    generation_route: GenerationRoute
    accepted_complex_plan_hash: Sha256 | None = None


class CachedGeneration(StrictFrozenModel):
    ir: RelationalQueryIR
    generation_route: GenerationRoute
    accepted_complex_plan_hash: Sha256 | None = None
    payload_sha256: Sha256


class ComplexPlanStep(StrictModel):
    step_id: NodeId
    operator_id: ComplexOperatorId
    depends_on: tuple[NodeId, ...] = ()
    input_object_ids: tuple[SemanticObjectId, ...]
    output_names: tuple[ColumnName, ...]


class ComplexQueryPlan(StrictModel):
    outcome: Literal["complex_plan"] = "complex_plan"
    plan_version: Literal["008.complex-plan.v1"]
    operator_ids: tuple[ComplexOperatorId, ...]
    steps: tuple[ComplexPlanStep, ...]
    expected_outputs: tuple[ColumnName, ...]



class SnapshotRanking(StrictFrozenModel):
    id: SemanticObjectId
    score: float

class CheckViolation(StrictFrozenModel):
    code: str
    subject: str = ""

class WarningRef(StrictFrozenModel):
    object_id: SemanticObjectId
    warning_hash: Sha256

class WarningDecision(WarningRef):
    control_id: str

class Assumption(StrictFrozenModel):
    kind: Literal["direction", "status", "grain", "snapshot"]
    column: ColumnRef
    operands: tuple[LiteralRef, ...]
    secondary_operands: tuple[LiteralRef, ...] = ()

class RequestedDisclosure(StrictFrozenModel):
    sources: tuple[ColumnRef, ...] = Field(min_length=1)

class GroundingNeed(StrictFrozenModel):
    kind: Literal["object"] = "object"
    object_id: SemanticObjectId

class ObjectAmbiguityCandidate(StrictModel):
    kind: Literal["object"] = "object"
    object_id: SemanticObjectId


class RelationshipAmbiguityCandidate(StrictModel):
    kind: Literal["relationship"] = "relationship"
    relationship_id: RelationshipId


class GovernedLiteralAmbiguityCandidate(StrictModel):
    kind: Literal["governed_literal"] = "governed_literal"
    literal_id: GovernedLiteralId


class GrainAmbiguityCandidate(StrictModel):
    kind: Literal["grain"] = "grain"
    grain: Literal["row", "day", "week", "month", "quarter", "year"]
    grouping_columns: tuple[ColumnRef, ...] = Field(min_length=1)


class OperatorAmbiguityCandidate(StrictModel):
    kind: Literal["operator"] = "operator"
    operator_id: ComplexOperatorId


AmbiguityCandidate = Annotated[
    ObjectAmbiguityCandidate | RelationshipAmbiguityCandidate |
    GovernedLiteralAmbiguityCandidate | GrainAmbiguityCandidate |
    OperatorAmbiguityCandidate,
    Field(discriminator="kind"),
]


class Ambiguity(StrictModel):
    ambiguity_id: NodeId
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    candidates: tuple[AmbiguityCandidate, ...] = Field(min_length=2)


class ClarificationRequest(StrictModel):
    outcome: Literal["clarification_request"] = "clarification_request"
    ambiguities: tuple[Ambiguity, ...] = Field(min_length=1)


class LiteralClarificationNeed(StrictModel):
    kind: Literal["literal_need"] = "literal_need"
    issue: Literal[
        "missing_literal", "invalid_question_span", "unparseable_question_literal",
        "ungrounded_governed_literal", "literal_type_mismatch", "invented_literal_reference",
    ]
    expected_type: ScalarType
    target_column: ColumnRef | None = None
    literal_ref: LiteralRef | None = None


class GroundingRefusal(StrictModel):
    outcome: Literal["grounding_refusal"] = "grounding_refusal"
    unmet_needs: tuple[GroundingNeed, ...]


IRGenerationOutcome = Annotated[
    RelationalQueryIR | ComplexQueryPlan | GroundingRefusal | ClarificationRequest,
    Field(discriminator="outcome"),
]


class BoundParameter(StrictFrozenModel):
    position: int = Field(ge=1)
    data_type: ScalarType
    value: JsonScalar = Field(exclude=True)


class CompiledQuery(StrictFrozenModel):
    sql: str
    parameters: tuple[BoundParameter, ...]
    ir_hash: Sha256
    compiler_version: str
    dialect: Literal["duckdb"]


class SQLArtifact(StrictFrozenModel):
    sql: str
    sql_sha256: Sha256
    parameter_count: int = Field(ge=0)
    parameter_types: tuple[ScalarType, ...]
    ir_hash: Sha256
    compiler_version: str
    dialect: Literal["duckdb"]


class BudgetLimits(StrictFrozenModel):
    initial_semantic_call_capacity: Literal[1] = 1
    planned_semantic_call_capacity: Literal[2] = 2
    max_transport_attempts_per_semantic_call: int = Field(default=2, ge=1, le=2)
    provider_timeout_ms_per_attempt: int = Field(default=20000, gt=0)
    max_input_tokens: int = Field(default=32000, gt=0)
    max_output_tokens: int = Field(default=8000, gt=0)
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0)
    end_to_end_deadline_ms: int = Field(default=120000, gt=0)

    @classmethod
    def defaults(cls) -> "BudgetLimits":
        return cls()


class BudgetUsage(StrictModel):
    semantic_call_capacity: Literal[1, 2]
    planned_ir_authorized: bool
    semantic_calls: int
    transport_attempts: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    elapsed_ms: int



class QueryResult(StrictFrozenModel):
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    row_count: int = Field(ge=0)
    truncated: bool
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def dimensions(self):
        if len(self.columns) != len(self.column_types) or len(self.rows) != self.row_count:
            raise ValueError("result_shape_mismatch")
        if len(set(self.columns)) != len(self.columns) or any(len(r) != len(self.columns) for r in self.rows):
            raise ValueError("result_shape_mismatch")
        return self

class OutputLineage(StrictFrozenModel):
    output_name: str
    data_type: ScalarType
    sources: tuple[ColumnRef, ...]
    classification: Classification
    reducing: bool = False
    metric_ids: tuple[MetricId, ...] = ()

class DisclosureRecord(StrictFrozenModel):
    output_name: str
    sources: tuple[ColumnRef, ...]
    row_bound: int = Field(gt=0, le=50)
    policy_id: PolicyId = "policy.sensitive-banking-data"

class GroundingUsage(StrictFrozenModel):
    object_ids: tuple[SemanticObjectId, ...] = ()
    warnings: tuple[WarningRef, ...] = ()
    metrics: tuple[SnapshotMetadataObject, ...] = ()

class AttemptRecord(StrictFrozenModel):
    domain: str
    phase: str
    ordinal: int = Field(ge=1)
    outcome: str
    elapsed_ms: int = Field(ge=0)
    codes: tuple[str, ...] = ()

class ManifestTable(StrictFrozenModel):
    name: ColumnName
    sha256: Sha256
    row_count: int = Field(ge=0)

class SourceManifest(StrictFrozenModel):
    source_kind: Literal["authoritative", "synthetic"] = "synthetic"
    version: Literal["008.source.v1"] = "008.source.v1"
    tables: tuple[ManifestTable, ...]

class MaterializedTableEvidence(StrictFrozenModel):
    table_id: TableId
    sha256: Sha256
    row_count: int = Field(ge=0)
    columns_sha256: Sha256

class MaterializationReceipt(StrictFrozenModel):
    source_kind: Literal["authoritative", "synthetic"]
    version: Literal["008.materialization.v1"] = "008.materialization.v1"
    manifest_sha256: Sha256
    bundle_sha256: Sha256
    database_sha256: Sha256
    engine_version: str
    tables: tuple[MaterializedTableEvidence, ...]
    receipt_sha256: Sha256

class ProviderCapabilityReceipt(StrictFrozenModel):
    run_id: str
    provider: str
    model: str
    model_revision: str
    schema_mechanism: Literal["json_schema"]
    verified_at: str
    request_sha256: Sha256
    receipt_sha256: Sha256

class SQLGenerationRequest(StrictModel):
    question: str
    authorization_scope: AuthorizationScope
    dialect: Literal["duckdb"] = "duckdb"
    max_rows: int = Field(default=1000, ge=1, le=1000)


class ResponseBase(StrictModel):
    contract_version: Literal["008.v3"]
    semantic_version: str
    policy_version: str
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    ir_contract_version: Literal["008.ir.v1"]
    type_registry_version: Literal["008.types.v1"]
    prompt_version: str
    router_version: str
    compiler_version: str
    checker_version: str
    dialect: Literal["duckdb"]
    provider: str
    model: str
    model_revision: str
    canonical_question_hash: Sha256
    authorization_scope_hash: Sha256
    snapshot_hash: Sha256 | None = None
    generation_route: GenerationRoute | Literal["none"]
    cache_status: Literal["disabled", "miss", "hit"]
    grounding_usage: GroundingUsage
    assumptions: tuple[Assumption, ...]
    attempt_records: tuple[AttemptRecord, ...]
    budget_usage: BudgetUsage
    violations: tuple[CheckViolation, ...] = ()


class OkResponse(ResponseBase):
    status: Literal["ok"] = "ok"
    generation_route: GenerationRoute
    snapshot_hash: Sha256
    ir: RelationalQueryIR
    sql_artifact: SQLArtifact
    result: QueryResult
    output_lineage: tuple[OutputLineage, ...]
    disclosures: tuple[DisclosureRecord, ...]


    @model_validator(mode="after")
    def complete_outputs(self):
        if tuple(x.output_name for x in self.output_lineage) != self.result.columns:
            raise ValueError("lineage_result_mismatch")
        if self.sql_artifact.ir_hash != __import__("cerebro.provenance", fromlist=["digest"]).digest(self.ir):
            raise ValueError("ir_hash_mismatch")
        return self


class CheckFailedResponse(ResponseBase):
    status: Literal["check_failed"] = "check_failed"
    executable: Literal[False] = False
    ir: RelationalQueryIR | None = None
    sql_artifact: SQLArtifact | None = None
    violations: tuple[CheckViolation, ...] = Field(min_length=1)


class RefusedResponse(ResponseBase):
    status: Literal["refused"] = "refused"
    reason: Literal[
        "missing_grounding", "policy_disallowed",
        "clarification_required", "unsupported_complexity",
    ]
    unmet_needs: tuple[GroundingNeed, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    literal_needs: tuple[LiteralClarificationNeed, ...] = ()
    policy_ids: tuple[PolicyId, ...] = ()
    unsupported_operator_ids: tuple[ComplexOperatorId, ...] = ()

    @model_validator(mode="after")
    def refusal_shape(self):
        if self.reason == "clarification_required":
            if not (self.ambiguities or self.literal_needs) or (self.ambiguities and self.literal_needs):
                raise ValueError("invalid_clarification_refusal")
        elif self.ambiguities or self.literal_needs:
            raise ValueError("unrelated_clarification_fields")
        if self.reason != "missing_grounding" and self.unmet_needs:
            raise ValueError("unrelated_grounding_needs")
        if self.reason != "policy_disallowed" and self.policy_ids:
            raise ValueError("unrelated_policy_fields")
        return self


SQLGenerationResponse = Annotated[
    OkResponse | CheckFailedResponse | RefusedResponse,
    Field(discriminator="status"),
]
for _model in tuple(globals().values()):
    if isinstance(_model, type) and issubclass(_model, BaseModel) and _model is not BaseModel:
        _model.model_rebuild()
OUTCOME_ADAPTER = TypeAdapter(IRGenerationOutcome)
IR_ADAPTER = TypeAdapter(RelationalQueryIR)
RESPONSE_ADAPTER = TypeAdapter(SQLGenerationResponse)
