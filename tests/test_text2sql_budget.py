from __future__ import annotations

from decimal import Decimal

import pytest
import text2sql_factories as factories

from cerebro.complexity import ComplexityRouter
from cerebro.models import BudgetLimits
from cerebro.text2sql import (
    BudgetExceeded,
    BudgetTransitionDenied,
    RequestBudget,
)


class FakeClock:
    """A deterministic clock: no test depends on real elapsed time."""

    def __init__(self) -> None:
        self._elapsed_ms = 0

    def advance_ms(self, milliseconds: int) -> None:
        self._elapsed_ms += milliseconds

    def monotonic_ms(self) -> int:
        return self._elapsed_ms


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


def _accepted(snapshot):
    return (
        ComplexityRouter()
        .validate(factories.complex_window_plan(snapshot), snapshot)
        .accepted
    )


def _budget_for(snapshot, clock, limits=None):
    """Start a budget already pinned to the snapshot orchestration resolved."""
    budget = RequestBudget.start(limits or BudgetLimits.defaults(), clock)
    budget.bind_snapshot(snapshot.snapshot_hash)
    return budget


# --- defaults --------------------------------------------------------------


def test_budget_defaults_are_exact():
    limits = BudgetLimits.defaults()
    assert limits.initial_semantic_call_capacity == 1
    assert limits.planned_semantic_call_capacity == 2
    assert limits.max_transport_attempts_per_semantic_call == 2
    assert limits.provider_timeout_ms_per_attempt == 20_000
    assert limits.max_input_tokens == 32_000
    assert limits.max_output_tokens == 8_000
    assert limits.max_cost_usd == Decimal("0.50")
    assert limits.end_to_end_deadline_ms == 120_000


def test_budget_starts_at_capacity_one(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    assert budget.semantic_call_capacity == 1
    assert budget.usage().semantic_calls == 0
    assert budget.usage().planned_ir_authorized is False


# --- semantic call capacity -------------------------------------------------


def test_normal_route_rejects_second_semantic_call(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    budget.before("default_ir")
    budget.record_semantic_call(
        input_tokens=100, output_tokens=20, cost_usd=Decimal("0.01")
    )
    with pytest.raises(BudgetExceeded) as caught:
        budget.before("default_ir")
    assert caught.value.code == "semantic_call_budget_exceeded"


def test_planned_ir_is_denied_before_any_router_acceptance(fake_clock, snapshot):
    budget = _budget_for(snapshot, fake_clock)
    with pytest.raises(BudgetExceeded):
        budget.before("planned_ir")
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(factories.foreign_accepted_route(snapshot))


def test_transition_without_a_pinned_snapshot_is_denied(fake_clock, snapshot):
    unpinned = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    with pytest.raises(BudgetTransitionDenied):
        unpinned.authorize_planned_ir(_accepted(snapshot))


def test_caller_created_or_absent_decision_is_denied(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    for forged in (None, object(), "planned_ir", {"generation_route": "planned_ir"}):
        with pytest.raises(BudgetTransitionDenied):
            budget.authorize_planned_ir(forged)


def test_mutated_accepted_plan_cannot_authorize_fallback(fake_clock, snapshot):
    budget = _budget_for(snapshot, fake_clock)
    accepted = _accepted(snapshot)
    # The plan is frozen, so tampering must fail at mutation time; if a future
    # change makes it mutable, the transition itself must still refuse.
    from pydantic import ValidationError

    try:
        accepted.plan.expected_outputs = ("tampered",)
    except (ValidationError, AttributeError, TypeError):
        mutated_blocked = True
    else:  # pragma: no cover - only if the contract loses immutability
        mutated_blocked = False
    if mutated_blocked:
        object.__setattr__(accepted, "plan_hash", "f" * 64)
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(accepted)


def test_accepted_transition_is_one_time_and_third_call_is_denied(fake_clock, snapshot):
    budget = _budget_for(snapshot, fake_clock)
    budget.before("default_ir")
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal(0))
    accepted = _accepted(snapshot)
    budget.authorize_planned_ir(accepted)
    assert budget.semantic_call_capacity == 2
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(accepted)
    budget.before("planned_ir")
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal(0))
    with pytest.raises(BudgetExceeded):
        budget.before("planned_ir")


def test_transition_requires_the_snapshot_it_was_bound_to(fake_clock, snapshot):
    budget = _budget_for(snapshot, fake_clock)
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(factories.foreign_accepted_route(snapshot))
    budget.authorize_planned_ir(_accepted(snapshot))
    assert budget.semantic_call_capacity == 2


# --- transport attempts ----------------------------------------------------


def test_transport_attempts_are_bounded_per_semantic_call(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    budget.before("default_ir")
    budget.record_transport_attempt()
    budget.record_transport_attempt()
    with pytest.raises(BudgetExceeded) as caught:
        budget.record_transport_attempt()
    assert caught.value.code == "transport_attempt_budget_exceeded"


def test_a_new_semantic_call_gets_its_own_transport_allowance(fake_clock, snapshot):
    budget = _budget_for(snapshot, fake_clock)
    budget.before("default_ir")
    budget.record_transport_attempt()
    budget.record_transport_attempt()
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal(0))
    budget.authorize_planned_ir(_accepted(snapshot))
    budget.before("planned_ir")
    budget.record_transport_attempt()
    assert budget.usage().transport_attempts == 3


def test_transport_attempts_are_never_erased(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    budget.before("default_ir")
    budget.record_transport_attempt()
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal(0))
    assert budget.usage().transport_attempts == 1


# --- monotonic token, cost, deadline ---------------------------------------


def test_token_budget_is_cumulative_across_calls(fake_clock, snapshot):
    limits = BudgetLimits.defaults().model_copy(update={"max_input_tokens": 150})
    budget = _budget_for(snapshot, fake_clock, limits)
    budget.before("default_ir")
    budget.record_semantic_call(input_tokens=100, output_tokens=1, cost_usd=Decimal(0))
    budget.authorize_planned_ir(_accepted(snapshot))
    with pytest.raises(BudgetExceeded) as caught:
        budget.before("planned_ir", estimated_tokens=100)
    assert caught.value.code == "token_budget_exceeded"


def test_cost_budget_is_cumulative_and_uses_decimal(fake_clock):
    limits = BudgetLimits.defaults().model_copy(
        update={"max_cost_usd": Decimal("0.02")}
    )
    budget = RequestBudget.start(limits, fake_clock)
    budget.before("default_ir")
    budget.record_semantic_call(
        input_tokens=1, output_tokens=1, cost_usd=Decimal("0.015")
    )
    with pytest.raises(BudgetExceeded) as caught:
        budget.before("compile", estimated_cost_usd=Decimal("0.01"))
    assert caught.value.code == "cost_budget_exceeded"
    assert budget.usage().cost_usd == Decimal("0.015")


def test_deadline_blocks_action_before_contact(fake_clock):
    budget = RequestBudget.start(
        BudgetLimits.defaults().model_copy(update={"end_to_end_deadline_ms": 10}),
        fake_clock,
    )
    fake_clock.advance_ms(11)
    with pytest.raises(BudgetExceeded) as caught:
        budget.before("engine_validation")
    assert caught.value.code == "deadline_exceeded"


def test_deadline_spans_engine_work_in_the_same_budget(fake_clock):
    budget = RequestBudget.start(
        BudgetLimits.defaults().model_copy(update={"end_to_end_deadline_ms": 100}),
        fake_clock,
    )
    budget.before("default_ir")
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal(0))
    fake_clock.advance_ms(60)
    budget.before("compile")
    fake_clock.advance_ms(60)
    with pytest.raises(BudgetExceeded):
        budget.before("execution")
    assert budget.usage().elapsed_ms >= 120


def test_usage_never_decreases(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    budget.before("default_ir")
    budget.record_semantic_call(
        input_tokens=10, output_tokens=5, cost_usd=Decimal("0.01")
    )
    first = budget.usage()
    with pytest.raises(BudgetExceeded):
        budget.before("default_ir")
    second = budget.usage()
    assert second.semantic_calls >= first.semantic_calls
    assert second.input_tokens >= first.input_tokens
    assert second.cost_usd >= first.cost_usd


def test_usage_is_reportable_on_every_terminal_response(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    usage = budget.usage()
    assert usage.semantic_call_capacity == 1
    assert usage.transport_attempts == 0
    assert usage.cost_usd == Decimal(0)


def test_engine_actions_are_checked_before_contact(fake_clock):
    budget = RequestBudget.start(
        BudgetLimits.defaults().model_copy(update={"max_output_tokens": 5}),
        fake_clock,
    )
    budget.before("default_ir")
    budget.record_semantic_call(input_tokens=1, output_tokens=5, cost_usd=Decimal(0))
    for action in ("compile", "ast_check", "engine_validation", "execution"):
        budget.before(action)
    with pytest.raises(BudgetExceeded):
        budget.before("planned_ir", estimated_tokens=1)


def test_unknown_action_is_rejected(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    with pytest.raises(ValueError):
        budget.before("sql_generation")
