"""Strict, metadata-only prompt envelope construction and membership authority.

Nothing here serializes a model and then deletes forbidden fields. Every
envelope is built field by field from an allowlist, so a value that is not
explicitly selected cannot reach egress by accident.
"""

from __future__ import annotations

import json
from typing import Literal, TypeAlias

from .models import (
    Classification,
    ColumnName,
    ComplexQueryPlan,
    GroundingSnapshot,
    GuardedGenerationRequest,
    JsonScalar,
    MetricId,
    PipelineStage,
    RelationshipId,
    ScalarType,
    SemanticObjectId,
    StrictFrozenModel,
    TableId,
    complex_plan_sha256,
)

PROMPT_VERSION = "008.prompt.v1"

# Fixed remediation table. The provider is told what to do differently in
# stable local wording; it never receives a raw checker message or a subject.
VIOLATION_REMEDIATIONS: dict[str, str] = {
    "unknown_table": "reference only tables present in the provided snapshot",
    "unknown_column": "reference only qualified columns present in the snapshot",
    "undeclared_join": "join only through a snapshot relationship",
    "unknown_metric": "use only a snapshot metric identifier",
    "non_boolean_filter": "make every filter predicate boolean",
    "invalid_function_signature": "use an allowlisted function with correct arity",
    "invalid_aggregate_placement": "place aggregates only in aggregate measures",
    "invalid_window_placement": "place window functions only in a window node",
    "nested_aggregate_or_window": "do not nest aggregates or window functions",
    "duplicate_output_alias": "make every output alias unique",
    "invalid_node_arity": "give every node its required inputs",
    "set_output_arity_mismatch": "make set-operation outputs match in arity",
    "set_output_type_mismatch": "make set-operation outputs match positionally by type",
    "invalid_clarification_request": "return spans and at least two grounded candidates",
    "unparsable_generation_outcome": "return one schema-valid outcome object",
}
_DEFAULT_REMEDIATION = "return a schema-valid, fully grounded outcome"

GuardedGenerationMode: TypeAlias = Literal["default_ir", "planned_ir", "provider_probe"]


class PromptColumnView(StrictFrozenModel):
    table_id: TableId
    column: ColumnName
    data_type: ScalarType
    classification: Classification


class PromptRelationshipView(StrictFrozenModel):
    relationship_id: RelationshipId
    left_table_id: TableId
    left_column: ColumnName
    right_table_id: TableId
    right_column: ColumnName


class PromptMetricView(StrictFrozenModel):
    metric_id: MetricId
    formula: str
    result_type: ScalarType


class PromptObjectView(StrictFrozenModel):
    object_id: SemanticObjectId
    object_type: Literal[
        "dataset", "table", "concept", "relationship", "metric", "policy"
    ]
    description: str = ""
    columns: tuple[PromptColumnView, ...] = ()


class PromptGovernedLiteralView(StrictFrozenModel):
    """An explicitly authored semantic constant, identified by its stable ID."""

    literal_id: str
    data_type: ScalarType
    value: JsonScalar


class GroundingPromptView(StrictFrozenModel):
    """The only snapshot projection that may leave the process."""

    semantic_version: str
    dialect: Literal["duckdb"]
    objects: tuple[PromptObjectView, ...] = ()
    relationships: tuple[PromptRelationshipView, ...] = ()
    metrics: tuple[PromptMetricView, ...] = ()
    governed_literals: tuple[PromptGovernedLiteralView, ...] = ()
    supports_window: bool
    supports_set_operations: bool


class PromptViolationView(StrictFrozenModel):
    """A prior failure reduced to a stable code, a phase token, and guidance."""

    code: str
    phase: PipelineStage
    remediation: str


class PromptEnvelope(StrictFrozenModel):
    mode: GuardedGenerationMode
    canonical_question: str
    snapshot: GroundingPromptView
    accepted_complex_plan: ComplexQueryPlan | None = None
    violations: tuple[PromptViolationView, ...] = ()


class ProviderProbe(StrictFrozenModel):
    """The only output shape the metadata-only capability probe accepts."""

    ok: Literal[True]


class PromptAuthorizationError(Exception):
    """Raised when an envelope input is not an exact snapshot member."""


def prompt_snapshot_view(snapshot: GroundingSnapshot) -> GroundingPromptView:
    """Project a snapshot into its allowlisted, metadata-only prompt view."""
    if not isinstance(snapshot, GroundingSnapshot):
        raise PromptAuthorizationError("a prompt view requires a frozen snapshot")

    objects: list[PromptObjectView] = []
    relationships: dict[str, PromptRelationshipView] = {}
    metrics: list[PromptMetricView] = []
    for item in snapshot.objects:
        objects.append(
            PromptObjectView(
                object_id=item.object_id,
                object_type=item.object_type,
                description=item.description,
                columns=tuple(
                    PromptColumnView(
                        table_id=column.ref.table_id,
                        column=column.ref.column,
                        data_type=column.data_type,
                        classification=column.classification,
                    )
                    for column in item.columns
                ),
            )
        )
        for relationship in item.relationships:
            relationships[relationship.relationship_id] = PromptRelationshipView(
                relationship_id=relationship.relationship_id,
                left_table_id=relationship.left.table_id,
                left_column=relationship.left.column,
                right_table_id=relationship.right.table_id,
                right_column=relationship.right.column,
            )
        if item.object_type == "metric":
            if item.formula is None or item.metric_result_type is None:
                raise PromptAuthorizationError(
                    "a metric view requires a governed formula and result type"
                )
            metrics.append(
                PromptMetricView(
                    metric_id=item.object_id,
                    formula=item.formula,
                    result_type=item.metric_result_type,
                )
            )

    return GroundingPromptView(
        semantic_version=snapshot.semantic_version,
        dialect=snapshot.dialect,
        objects=tuple(objects),
        relationships=tuple(relationships[key] for key in sorted(relationships)),
        metrics=tuple(metrics),
        governed_literals=tuple(
            PromptGovernedLiteralView(
                literal_id=literal.literal_id,
                data_type=literal.data_type,
                value=literal.value,
            )
            for literal in snapshot.governed_literals
        ),
        supports_window=snapshot.dialect_capabilities.supports_window,
        supports_set_operations=snapshot.dialect_capabilities.supports_set_operations,
    )


_GOVERNED_NAMESPACES = (
    "dataset.",
    "table.",
    "concept.",
    "relationship.",
    "metric.",
    "policy.",
    "literal.",
)


def _claims_governed_membership(subject: str) -> bool:
    return subject.startswith(_GOVERNED_NAMESPACES)


def _prior_violation_subjects(
    request: GuardedGenerationRequest,
) -> tuple[str, ...]:
    return tuple(
        subject
        for violation in request.prior_violations
        for subject in violation.subject_ids
    )


def _snapshot_member_ids(snapshot: GroundingSnapshot) -> frozenset[str]:
    members: set[str] = set(snapshot.authorized_object_ids)
    for item in snapshot.objects:
        members.add(item.object_id)
        for column in item.columns:
            members.add(f"{column.ref.table_id}.{column.ref.column}")
        for relationship in item.relationships:
            members.add(relationship.relationship_id)
    for literal in snapshot.governed_literals:
        members.add(literal.literal_id)
    return frozenset(members)


def _build_prompt_envelope(request: GuardedGenerationRequest) -> PromptEnvelope:
    """Build an envelope field by field from authorized request state only."""
    view = prompt_snapshot_view(request.snapshot)
    plan: ComplexQueryPlan | None = None
    if request.mode == "planned_ir":
        route = request.accepted_complex_route
        if route is None:
            raise PromptAuthorizationError(
                "the planned mode requires an accepted route"
            )
        # Recompute the binding immediately before rendering so a mutation that
        # slipped past construction cannot reach egress.
        if route.snapshot_hash != request.snapshot.snapshot_hash:
            raise PromptAuthorizationError(
                "the accepted route is not bound to this snapshot"
            )
        if route.plan_hash != complex_plan_sha256(route.plan):
            raise PromptAuthorizationError("the accepted plan hash no longer matches")
        plan = route.plan
    violations = tuple(
        PromptViolationView(
            code=violation.code,
            phase=violation.stage,
            remediation=VIOLATION_REMEDIATIONS.get(
                violation.code, _DEFAULT_REMEDIATION
            ),
        )
        for violation in request.prior_violations
    )
    return PromptEnvelope(
        mode=request.mode,
        canonical_question=request.canonical_question,
        snapshot=view,
        accepted_complex_plan=plan,
        violations=violations,
    )


def _authorize_prompt_envelope(
    envelope: PromptEnvelope, request: GuardedGenerationRequest
) -> None:
    """Revalidate exact membership for everything the envelope would send."""
    members = _snapshot_member_ids(request.snapshot)
    for item in envelope.snapshot.objects:
        if item.object_id not in members:
            raise PromptAuthorizationError("envelope object is not a snapshot member")
        for column in item.columns:
            if f"{column.table_id}.{column.column}" not in members:
                raise PromptAuthorizationError(
                    "envelope column is not a snapshot member"
                )
    for relationship in envelope.snapshot.relationships:
        if relationship.relationship_id not in members:
            raise PromptAuthorizationError(
                "envelope relationship is not a snapshot member"
            )
        if (
            relationship.left_table_id not in members
            or relationship.right_table_id not in members
        ):
            raise PromptAuthorizationError(
                "envelope relationship endpoint is not a snapshot member"
            )
    for metric in envelope.snapshot.metrics:
        if metric.metric_id not in members:
            raise PromptAuthorizationError("envelope metric is not a snapshot member")
    for literal in envelope.snapshot.governed_literals:
        if literal.literal_id not in members:
            raise PromptAuthorizationError(
                "envelope governed literal is not a snapshot member"
            )
    # Subjects never enter the envelope, but a subject that *claims* to name a
    # governed object must still be a real member: a valid-looking nonmember is
    # an egress attempt, not a hint. An IR-local node ID claims nothing.
    for subject in _prior_violation_subjects(request):
        if _claims_governed_membership(subject) and subject not in members:
            raise PromptAuthorizationError(
                "a prior violation subject is not a snapshot member"
            )
    if envelope.canonical_question != request.canonical_question:
        raise PromptAuthorizationError(
            "envelope question is not the canonical question"
        )
    if envelope.mode != request.mode:
        raise PromptAuthorizationError("envelope mode does not match the request")


def _render_prompt(envelope: PromptEnvelope) -> str:
    """Render the envelope as canonical JSON, with no free-form text channel."""
    return json.dumps(
        envelope.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def empty_probe_snapshot(reference: GroundingSnapshot) -> GroundingSnapshot:
    """Return a metadata-free snapshot for the capability probe."""
    return reference.model_copy(
        update={
            "objects": (),
            "governed_literals": (),
            "ranking_evidence": (),
            "authorized_object_ids": frozenset(),
            "policy_ids": frozenset(),
        }
    )
