"""Local complex-plan validation and the only mint path for planned routing.

The router is deliberately inert: it reads a plan and a snapshot, decides, and
returns an opaque capability. It never contacts a provider, compiles SQL, or
touches a budget, so escalation authority cannot leak out of local validation.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .models import (
    AcceptedComplexRoute,
    ComplexQueryPlan,
    GroundingSnapshot,
    _create_accepted_complex_route,
    complex_plan_sha256,
)
from .models import (
    CheckViolation as _CheckViolation,
)

COMPLEX_OPERATOR_ALLOWLIST = frozenset(
    {"window.period_over_period.v1", "set_operation.safe_binary.v1"}
)


class ComplexityDecision:
    """The outcome of one local escalation review."""

    __slots__ = ("accepted", "reason", "violation")

    def __init__(
        self,
        *,
        accepted: AcceptedComplexRoute | None = None,
        reason: str | None = None,
        violation: _CheckViolation | None = None,
    ) -> None:
        self.accepted = accepted
        self.reason = reason
        self.violation = violation


def _unsupported(subject_ids: tuple[str, ...] = ()) -> ComplexityDecision:
    return ComplexityDecision(
        accepted=None,
        reason="unsupported_complexity",
        violation=_CheckViolation(
            code="unsupported_complexity",
            stage="complexity",
            subject_ids=subject_ids,
        ),
    )


class ComplexityRouter:
    """Accept escalation only for an allowlisted, fully grounded decomposition."""

    def validate(self, plan: Any, snapshot: GroundingSnapshot) -> ComplexityDecision:
        if not isinstance(snapshot, GroundingSnapshot):
            raise TypeError("escalation requires a frozen grounding snapshot")

        # A provider payload arrives as a mapping, so validate it here rather
        # than trusting a caller to have constructed the model already.
        if isinstance(plan, ComplexQueryPlan):
            validated = plan
        elif isinstance(plan, dict):
            try:
                validated = ComplexQueryPlan.model_validate(plan)
            except ValidationError:
                return _unsupported()
        else:
            return _unsupported()

        declared_operators = set(validated.operator_ids)
        if not declared_operators <= COMPLEX_OPERATOR_ALLOWLIST:
            return _unsupported(
                tuple(sorted(declared_operators - COMPLEX_OPERATOR_ALLOWLIST))
            )

        step_ids = [step.step_id for step in validated.steps]
        if len(step_ids) != len(set(step_ids)):
            return _unsupported(tuple(sorted(step_ids)))

        known_steps = set(step_ids)
        for step in validated.steps:
            if step.operator_id not in declared_operators:
                return _unsupported((step.step_id,))
            if step.step_id in step.depends_on:
                return _unsupported((step.step_id,))
            if not set(step.depends_on) <= known_steps:
                return _unsupported((step.step_id,))
            # Every referenced object must be an exact snapshot member, so a
            # decomposition cannot name something retrieval never authorized.
            if not set(step.input_object_ids) <= set(snapshot.authorized_object_ids):
                return _unsupported((step.step_id,))

        if _has_dependency_cycle(validated):
            return _unsupported(tuple(sorted(step_ids)))

        declared_outputs = {
            name for step in validated.steps for name in step.output_names
        }
        if not set(validated.expected_outputs) <= declared_outputs:
            return _unsupported(
                tuple(sorted(set(validated.expected_outputs) - declared_outputs))
            )

        accepted = _create_accepted_complex_route(
            snapshot_hash=snapshot.snapshot_hash,
            plan_hash=complex_plan_sha256(validated),
            plan=validated,
        )
        return ComplexityDecision(accepted=accepted)


def _has_dependency_cycle(plan: ComplexQueryPlan) -> bool:
    dependencies = {step.step_id: set(step.depends_on) for step in plan.steps}
    visiting: set[str] = set()
    settled: set[str] = set()

    def walk(step_id: str) -> bool:
        if step_id in visiting:
            return True
        if step_id in settled:
            return False
        visiting.add(step_id)
        for parent in dependencies.get(step_id, set()):
            if walk(parent):
                return True
        visiting.discard(step_id)
        settled.add(step_id)
        return False

    return any(walk(step_id) for step_id in dependencies)
