from __future__ import annotations

import dataclasses
import inspect
import json

import pytest
import text2sql_factories as factories

from cerebro.complexity import COMPLEX_OPERATOR_ALLOWLIST, ComplexityRouter
from cerebro.models import (
    AcceptedComplexRoute,
    ComplexPlanStep,
    ComplexQueryPlan,
    GuardedGenerationRequest,
    complex_plan_sha256,
)


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


def _plan(snapshot=None) -> ComplexQueryPlan:
    return factories.complex_window_plan(snapshot)


# --- acceptance ------------------------------------------------------------


def test_supported_complex_plan_produces_bound_acceptance(snapshot):
    plan = _plan(snapshot)
    decision = ComplexityRouter().validate(plan, snapshot)
    assert isinstance(decision.accepted, AcceptedComplexRoute)
    assert decision.accepted.generation_route == "planned_ir"
    assert decision.accepted.snapshot_hash == snapshot.snapshot_hash
    assert decision.accepted.plan_hash == complex_plan_sha256(plan)
    assert decision.reason is None
    assert decision.violation is None


def test_acceptance_is_usable_as_planned_generation_authority(snapshot):
    decision = ComplexityRouter().validate(_plan(snapshot), snapshot)
    request = GuardedGenerationRequest(
        mode="planned_ir",
        canonical_question=factories.canonical_question(),
        snapshot=snapshot,
        accepted_complex_route=decision.accepted,
        prior_violations=(),
    )
    assert request.accepted_complex_route is decision.accepted


def test_acceptance_stays_opaque_and_non_wire(snapshot):
    accepted = ComplexityRouter().validate(_plan(snapshot), snapshot).accepted
    assert not hasattr(accepted, "model_dump")
    assert not hasattr(accepted, "model_validate")
    with pytest.raises(TypeError):
        dataclasses.replace(accepted, plan_hash="f" * 64)
    with pytest.raises(TypeError):
        json.dumps(accepted)  # type: ignore[arg-type]


def test_set_operation_plan_is_also_allowlisted(snapshot):
    plan = ComplexQueryPlan(
        outcome="complex_plan",
        plan_version="008.complex-plan.v1",
        operator_ids=("set_operation.safe_binary.v1",),
        steps=(
            ComplexPlanStep(
                step_id="combine_activity",
                operator_id="set_operation.safe_binary.v1",
                depends_on=(),
                input_object_ids=("table.transactions", "table.accounts"),
                output_names=("account_id",),
            ),
        ),
        expected_outputs=("account_id",),
    )
    decision = ComplexityRouter().validate(plan, snapshot)
    assert decision.accepted is not None


def test_allowlist_is_exactly_the_two_initial_operators():
    assert COMPLEX_OPERATOR_ALLOWLIST == frozenset(
        {"window.period_over_period.v1", "set_operation.safe_binary.v1"}
    )


# --- refusal --------------------------------------------------------------


def test_mixed_supported_and_unsupported_plan_refuses(snapshot):
    plan = _plan(snapshot)
    payload = plan.model_dump(mode="python")
    payload["operator_ids"] = (
        "window.period_over_period.v1",
        "recursive_query.v1",
    )
    decision = ComplexityRouter().validate(payload, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"


def test_step_operator_outside_the_plan_declaration_refuses(snapshot):
    plan = _plan(snapshot)
    payload = plan.model_dump(mode="python")
    payload["operator_ids"] = ("set_operation.safe_binary.v1",)
    decision = ComplexityRouter().validate(payload, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"


def test_unknown_step_dependency_refuses(snapshot):
    plan = _plan(snapshot)
    broken = plan.model_copy(
        update={
            "steps": (
                plan.steps[0].model_copy(update={"depends_on": ("absent_step",)}),
            )
        }
    )
    decision = ComplexityRouter().validate(broken, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"


def test_cyclic_step_dependency_refuses(snapshot):
    plan = _plan(snapshot)
    step = plan.steps[0]
    broken = plan.model_copy(
        update={"steps": (step.model_copy(update={"depends_on": (step.step_id,)}),)}
    )
    decision = ComplexityRouter().validate(broken, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"


def test_nonmember_input_object_refuses(snapshot):
    plan = _plan(snapshot)
    broken = plan.model_copy(
        update={
            "steps": (
                plan.steps[0].model_copy(update={"input_object_ids": ("table.atms",)}),
            )
        }
    )
    decision = ComplexityRouter().validate(broken, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"


def test_expected_output_not_declared_by_any_step_refuses(snapshot):
    plan = _plan(snapshot)
    broken = plan.model_copy(update={"expected_outputs": ("undeclared_output",)})
    decision = ComplexityRouter().validate(broken, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"


def test_plan_from_another_snapshot_cannot_be_accepted_here(snapshot):
    other = snapshot.model_copy(update={"snapshot_hash": "e" * 64})
    decision = ComplexityRouter().validate(_plan(snapshot), other)
    accepted = decision.accepted
    if accepted is not None:
        # Acceptance must bind the snapshot it was validated against.
        assert accepted.snapshot_hash == other.snapshot_hash


def test_router_rejects_a_non_plan_payload(snapshot):
    for payload in ("plan", 7, None, {"outcome": "ir"}):
        decision = ComplexityRouter().validate(payload, snapshot)
        assert decision.accepted is None
        assert decision.reason == "unsupported_complexity"


# --- the router stays local ------------------------------------------------


def test_router_contacts_no_provider_compiler_or_budget():
    source = inspect.getsource(ComplexityRouter)
    for forbidden in (
        "GuardedProvider",
        "OrganizerModelGateway",
        "authorize_planned_ir",
        "compile",
        "duckdb",
        "httpx",
    ):
        assert forbidden not in source


def test_router_never_exposes_the_private_token_or_factory():
    from cerebro import complexity

    source = inspect.getsource(complexity)
    assert "_ACCEPTED_COMPLEX_ROUTE_TOKEN" not in source
    assert not hasattr(complexity, "_ACCEPTED_COMPLEX_ROUTE_TOKEN")


# --- shared factories ------------------------------------------------------


def test_factory_accepted_route_runs_the_router(snapshot):
    accepted = factories.accepted_complex_route(snapshot)
    assert isinstance(accepted, AcceptedComplexRoute)
    assert accepted.snapshot_hash == snapshot.snapshot_hash
    assert accepted.plan_hash == complex_plan_sha256(accepted.plan)


def test_factory_foreign_route_is_bound_to_a_different_snapshot(snapshot):
    foreign = factories.foreign_accepted_route(snapshot)
    assert isinstance(foreign, AcceptedComplexRoute)
    assert foreign.snapshot_hash != snapshot.snapshot_hash
    with pytest.raises((TypeError, ValueError)):
        GuardedGenerationRequest(
            mode="planned_ir",
            canonical_question=factories.canonical_question(),
            snapshot=snapshot,
            accepted_complex_route=foreign,
            prior_violations=(),
        )


def test_factories_still_mint_no_capability_directly():
    source = inspect.getsource(factories)
    assert "_create_accepted_complex_route" not in source
    assert "_ACCEPTED_COMPLEX_ROUTE_TOKEN" not in source
    assert "ComplexityRouter" in source
