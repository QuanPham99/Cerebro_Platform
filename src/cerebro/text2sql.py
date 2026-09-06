"""Budget primitives for one Text-to-SQL request.

Task 8 owns only the accounting: capacity, transport attempts, tokens, cost, and
the end-to-end deadline. Orchestration arrives in Task 10. Every counter here is
monotonic, and the single capacity transition is one-way and capability-gated.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Protocol

from .models import AcceptedComplexRoute, BudgetLimits, BudgetUsage, complex_plan_sha256

# Actions that consume a semantic model call. Everything else is local work.
_SEMANTIC_ACTIONS = frozenset({"default_ir", "planned_ir"})
_LOCAL_ACTIONS = frozenset(
    {
        "snapshot",
        "cache",
        "clarification",
        "complexity",
        "literal_resolution",
        "compile",
        "ast_check",
        "engine_validation",
        "execution",
        "provider_transport",
    }
)


class Clock(Protocol):
    def monotonic_ms(self) -> int: ...


class _SystemClock:
    def monotonic_ms(self) -> int:
        return int(time.monotonic() * 1000)


class BudgetExceeded(Exception):
    """Raised before any contact when an action would exceed a limit."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BudgetTransitionDenied(Exception):
    """Raised when a capacity transition is not authorized by the router."""

    def __init__(self, code: str = "planned_ir_transition_denied") -> None:
        super().__init__(code)
        self.code = code


class RequestBudget:
    """One request's monotonic budget across provider, compiler, and engine."""

    def __init__(self, limits: BudgetLimits, clock: Clock) -> None:
        self._limits = limits
        self._clock = clock
        self._started_ms = clock.monotonic_ms()
        # Capacity always starts at one, whatever shape the request has.
        self._capacity = limits.initial_semantic_call_capacity
        self._planned_authorized = False
        self._semantic_calls = 0
        self._transport_attempts = 0
        self._attempts_in_current_call = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cost_usd = Decimal(0)
        self._bound_snapshot_hash: str | None = None
        self._authorized_plan_hash: str | None = None

    @classmethod
    def start(cls, limits: BudgetLimits, clock: Clock | None = None) -> RequestBudget:
        return cls(limits, clock or _SystemClock())

    @property
    def semantic_call_capacity(self) -> int:
        return self._capacity

    def bind_snapshot(self, snapshot_hash: str) -> None:
        """Pin the snapshot a later escalation must be bound to."""
        self._bound_snapshot_hash = snapshot_hash

    def usage(self) -> BudgetUsage:
        return BudgetUsage(
            semantic_call_capacity=self._capacity,
            planned_ir_authorized=self._planned_authorized,
            semantic_calls=self._semantic_calls,
            transport_attempts=self._transport_attempts,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cost_usd=self._cost_usd,
            elapsed_ms=self._elapsed_ms(),
        )

    def _elapsed_ms(self) -> int:
        return max(0, self._clock.monotonic_ms() - self._started_ms)

    def before(
        self,
        action: str,
        estimated_tokens: int = 0,
        estimated_cost_usd: Decimal = Decimal(0),
    ) -> None:
        """Check every limit before an action makes any contact."""
        if action not in _SEMANTIC_ACTIONS and action not in _LOCAL_ACTIONS:
            raise ValueError(f"unknown budget action: {action}")

        if self._elapsed_ms() > self._limits.end_to_end_deadline_ms:
            raise BudgetExceeded("deadline_exceeded")

        if action in _SEMANTIC_ACTIONS:
            if action == "planned_ir" and not self._planned_authorized:
                raise BudgetExceeded("planned_route_not_authorized")
            if self._semantic_calls >= self._capacity:
                raise BudgetExceeded("semantic_call_budget_exceeded")

        if estimated_tokens < 0:
            raise ValueError("estimated tokens cannot be negative")
        if self._input_tokens + estimated_tokens > self._limits.max_input_tokens:
            raise BudgetExceeded("token_budget_exceeded")
        if self._output_tokens > self._limits.max_output_tokens:
            raise BudgetExceeded("token_budget_exceeded")
        if self._cost_usd + estimated_cost_usd > self._limits.max_cost_usd:
            raise BudgetExceeded("cost_budget_exceeded")

        if action in _SEMANTIC_ACTIONS:
            # A new semantic call gets its own transport allowance; the total
            # attempt count still only ever grows.
            self._attempts_in_current_call = 0

    def authorize_planned_ir(self, decision: Any) -> None:
        """Perform the one-way capacity transition, only for authentic authority."""
        if self._planned_authorized:
            raise BudgetTransitionDenied("transition_already_used")
        if not isinstance(decision, AcceptedComplexRoute):
            raise BudgetTransitionDenied("decision_not_authentic")
        if getattr(decision, "generation_route", None) != "planned_ir":
            raise BudgetTransitionDenied("decision_not_planned_route")
        try:
            recomputed = complex_plan_sha256(decision.plan)
        except TypeError as error:
            raise BudgetTransitionDenied("decision_plan_invalid") from error
        if recomputed != decision.plan_hash:
            # A mutated or stale plan can never buy extra capacity.
            raise BudgetTransitionDenied("decision_plan_hash_mismatch")
        if self._bound_snapshot_hash is None:
            # Without a pinned snapshot there is nothing to validate the
            # decision against, so escalation is refused.
            raise BudgetTransitionDenied("snapshot_not_bound")
        if decision.snapshot_hash != self._bound_snapshot_hash:
            raise BudgetTransitionDenied("decision_snapshot_mismatch")

        self._capacity = self._limits.planned_semantic_call_capacity
        self._planned_authorized = True
        self._authorized_plan_hash = decision.plan_hash

    def record_transport_attempt(self) -> None:
        """Count one HTTP attempt inside the current semantic call."""
        allowance = self._limits.max_transport_attempts_per_semantic_call
        if self._attempts_in_current_call >= allowance:
            raise BudgetExceeded("transport_attempt_budget_exceeded")
        self._attempts_in_current_call += 1
        self._transport_attempts += 1

    def record_semantic_call(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
    ) -> None:
        """Record actual usage after a semantic call. Counters never decrease."""
        if input_tokens < 0 or output_tokens < 0 or cost_usd < 0:
            raise ValueError("recorded usage cannot be negative")
        self._semantic_calls += 1
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._cost_usd += cost_usd

    def record_local_usage(self, *, cost_usd: Decimal = Decimal(0)) -> None:
        """Record compiler or engine cost inside the same request budget."""
        if cost_usd < 0:
            raise ValueError("recorded usage cannot be negative")
        self._cost_usd += cost_usd
