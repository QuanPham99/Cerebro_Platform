"""Budget primitives for one Text-to-SQL request.

Task 8 owns only the accounting: capacity, transport attempts, tokens, cost, and
the end-to-end deadline. Orchestration arrives in Task 10. Every counter here is
monotonic, and the single capacity transition is one-way and capability-gated.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Protocol

from .models import (
    AcceptedComplexRoute,
    BudgetLimits,
    BudgetUsage,
    CachedGeneration,
    complex_plan_sha256,
)

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


# ---------------------------------------------------------------------------
# Orchestration (Task 10).
#
# One semantic call by default, one guarded second call only after the local
# router accepts an escalation, and every gate in a fixed order. No failure
# after generation contacts the provider again.
# ---------------------------------------------------------------------------

from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from .models import (
    AttemptRecord,
    CheckFailedResponse,
    CheckViolation,
    ClarificationRequest,
    ComplexQueryPlan,
    GroundingRefusal,
    GroundingUsage,
    IRGenerationOutcome,
    LiteralClarificationNeed,
    OkResponse,
    RefusedResponse,
    RelationalQueryIR,
    SQLGenerationRequest,
    relational_ir_sha256,
)
from .prompting import PROMPT_VERSION
from .provenance import canonical_question_sha256, canonicalize_question
from .selfcheck import (
    DisclosureCaps,
    authorize_compiled_query,
    check_grounding_refusal,
    validate_clarification,
    validate_ir,
)
from .sql_compiler import CompilerError, to_sql_artifact
from .text2sql_cache import CacheIntegrityError, CacheKey, cache_payload_sha256
from .text2sql_provider import (
    EgressBlocked,
    ProviderConfigurationError,
    ProviderRejected,
    ProviderUnavailable,
)


@dataclass(frozen=True)
class RuntimeVersions:
    """Versions the response must report and the cache key must include."""

    semantic_version: str
    policy_version: str
    prompt_version: str = PROMPT_VERSION
    router_version: str = "008.router.v1"
    checker_version: str = "008.checker.v1"
    ir_contract_version: str = "008.ir.v1"
    type_registry_version: str = "008.types.v1"
    canonicalization_version: str = "008.question.v1"
    literal_registry_version: str = "008.literal-span.v1"


class _Terminal(Exception):
    """Internal control flow: a terminal response is ready."""

    def __init__(self, response: Any) -> None:
        super().__init__("terminal")
        self.response = response


class Text2SQLAgent:
    """Orchestrate one request end to end with exactly one gate order."""

    def __init__(
        self,
        *,
        resolver: Any,
        provider: Any,
        router: Any,
        compiler: Any,
        cache: Any,
        validator: Any,
        executor: Any,
        disclosure_caps: DisclosureCaps,
        budget_limits: BudgetLimits,
        versions: RuntimeVersions,
    ) -> None:
        dependencies = {
            "resolver": resolver,
            "provider": provider,
            "router": router,
            "compiler": compiler,
            "cache": cache,
            "validator": validator,
            "executor": executor,
            "disclosure_caps": disclosure_caps,
            "budget_limits": budget_limits,
            "versions": versions,
        }
        missing = [name for name, value in dependencies.items() if value is None]
        if missing:
            # Every dependency is mandatory: a missing gate is not a default.
            raise ValueError(f"missing required dependencies: {', '.join(missing)}")
        self._resolver = resolver
        self._provider = provider
        self._router = router
        self._compiler = compiler
        self._cache = cache
        self._validator = validator
        self._executor = executor
        self._caps = disclosure_caps
        self._limits = budget_limits
        self._versions = versions

    @property
    def compiler_version(self) -> str:
        from .sql_compiler import compiler_version

        return compiler_version()

    # -- public entry point ----------------------------------------------
    def run(self, request: SQLGenerationRequest) -> Any:
        if not isinstance(request, SQLGenerationRequest):
            raise TypeError("the agent accepts only a validated SQLGenerationRequest")
        state = _RunState(self, request)
        try:
            return state.execute()
        except _Terminal as terminal:
            return terminal.response


class _RunState:
    """One request's mutable progress. It exists for exactly one `run` call."""

    def __init__(self, agent: Text2SQLAgent, request: SQLGenerationRequest) -> None:
        self.agent = agent
        self.request = request
        self.budget = RequestBudget.start(agent._limits)
        self.attempts: list[AttemptRecord] = []
        self.violations: list[CheckViolation] = []
        self.route: str = "none"
        self.cache_status: str = "disabled"
        self.snapshot: Any = None
        self.canonical_question: str = ""
        self.question_hash: str = ""
        self.grounding_usage = GroundingUsage()

    # -- bookkeeping ------------------------------------------------------
    def record(self, stage: str, outcome: str, codes: tuple[str, ...] = ()) -> None:
        self.attempts.append(
            AttemptRecord(
                stage=stage,
                ordinal=len(self.attempts) + 1,
                outcome=outcome,
                latency_ms=0,
                violation_codes=codes,
                generation_route=self.route,
                cache_status=self.cache_status,
            )
        )

    def _base_fields(self) -> dict[str, Any]:
        versions = self.agent._versions
        return {
            "contract_version": "008.v3",
            "semantic_version": versions.semantic_version,
            "policy_version": versions.policy_version,
            "canonicalization_version": "008.question.v1",
            "literal_registry_version": "008.literal-span.v1",
            "ir_contract_version": "008.ir.v1",
            "type_registry_version": "008.types.v1",
            "prompt_version": versions.prompt_version,
            "router_version": versions.router_version,
            "compiler_version": self.agent.compiler_version,
            "checker_version": versions.checker_version,
            "dialect": "duckdb",
            "provider": getattr(self.agent._provider, "provider", "unknown"),
            "model": getattr(self.agent._provider, "model", "unknown"),
            "model_revision": getattr(
                self.agent._provider, "model_revision", "unknown"
            ),
            "canonical_question_hash": self.question_hash,
            "authorization_scope_hash": self.request.authorization_scope.authorization_scope_hash,
            "snapshot_hash": self.snapshot.snapshot_hash if self.snapshot else None,
            "generation_route": self.route,
            "cache_status": self.cache_status,
            "grounding_usage": self.grounding_usage,
            "assumptions": (),
            "attempt_records": tuple(self.attempts),
            "budget_usage": self.budget.usage(),
            "violations": tuple(self.violations),
        }

    def check_failed(self, *codes_and_stage: tuple[str, str], ir=None, artifact=None):
        for code, stage in codes_and_stage:
            violation = CheckViolation(code=code, stage=stage, subject_ids=())
            if violation not in self.violations:
                self.violations.append(violation)
        if not self.violations:
            self.violations.append(
                CheckViolation(code="unspecified_failure", stage="snapshot")
            )
        raise _Terminal(
            CheckFailedResponse(
                **{
                    **self._base_fields(),
                    "status": "check_failed",
                    "executable": False,
                },
                ir=ir,
                sql_artifact=artifact,
            )
        )

    def check_failed_with(self, violations: tuple[CheckViolation, ...], ir=None):
        for violation in violations:
            if violation not in self.violations:
                self.violations.append(violation)
        raise _Terminal(
            CheckFailedResponse(
                **{
                    **self._base_fields(),
                    "status": "check_failed",
                    "executable": False,
                },
                ir=ir,
                sql_artifact=None,
            )
        )

    def refuse(self, reason: str, **populations: Any):
        raise _Terminal(
            RefusedResponse(
                **{**self._base_fields(), "status": "refused", "reason": reason},
                **populations,
            )
        )

    # -- the fixed gate order --------------------------------------------
    def execute(self) -> Any:
        agent = self.agent

        # 1-2. Canonicalize, then resolve and pin the authorized snapshot.
        self.canonical_question = canonicalize_question(self.request.question)
        self.question_hash = canonical_question_sha256(self.canonical_question)
        self.budget.before("snapshot")
        self.snapshot = agent._resolver.resolve(
            self.canonical_question,
            self.request.authorization_scope,
            self.request.dialect,
        )
        self.budget.bind_snapshot(self.snapshot.snapshot_hash)
        self.record("snapshot", "accepted")

        # 3. Probe the cache for this question's original generation route.
        validated = self._probe_cache()

        if validated is None:
            # 4-6. One default call, then at most one guarded planned call.
            validated = self._generate()

        # 7-12. Local gates, compilation, authorization, engine, response.
        return self._finish(validated)

    # -- cache -----------------------------------------------------------
    def _cache_key(self, route: str) -> CacheKey:
        versions = self.agent._versions
        provider = self.agent._provider
        return CacheKey(
            authorization_scope_hash=self.snapshot.authorization_scope_hash,
            snapshot_hash=self.snapshot.snapshot_hash,
            policy_version=self.snapshot.policy_version,
            canonicalization_version="008.question.v1",
            canonical_question_hash=self.question_hash,
            literal_registry_version="008.literal-span.v1",
            dialect="duckdb",
            generation_route=route,
            provider=getattr(provider, "provider", "unknown"),
            model=getattr(provider, "model", "unknown"),
            model_revision=getattr(provider, "model_revision", "unknown"),
            schema_mechanism=getattr(provider, "schema_mechanism", "json_schema"),
            prompt_version=versions.prompt_version,
            ir_contract_version="008.ir.v1",
            router_version=versions.router_version,
            compiler_version=self.agent.compiler_version,
            type_registry_version="008.types.v1",
            checker_version=versions.checker_version,
        )

    def _probe_cache(self):
        self.budget.before("cache")
        try:
            found = self.agent._cache.probe(
                self._cache_key("default_ir").without_route()
            )
        except CacheIntegrityError:
            self.cache_status = "miss"
            self.record("cache", "rejected", ("cache_integrity_error",))
            self.check_failed(("cache_integrity_error", "cache"))
        if found is None:
            self.cache_status = "miss"
            self.record("cache", "rejected")
            return None
        route, value = found
        self.route = route
        self.cache_status = "hit"
        self.record("cache", "accepted")
        return self.agent._cache.reconstruct_validated_ir(value, self._cache_key(route))

    # -- generation ------------------------------------------------------
    def _call_provider(self, mode: str, adapter: TypeAdapter, plan_route=None):
        from .models import GuardedGenerationRequest

        stage = "default_ir" if mode == "default_ir" else "planned_ir"
        self.budget.before(stage)
        guarded = GuardedGenerationRequest(
            mode=mode,
            canonical_question=self.canonical_question,
            snapshot=self.snapshot,
            accepted_complex_route=plan_route,
            prior_violations=tuple(self.violations),
        )
        try:
            generation = self.agent._provider.generate(guarded, adapter)
        except EgressBlocked:
            self.record(stage, "rejected", ("egress_blocked",))
            self.check_failed(("egress_blocked", stage))
        except ProviderConfigurationError:
            self.record(
                "provider_transport", "rejected", ("provider_configuration_error",)
            )
            self.check_failed(("provider_configuration_error", "provider_transport"))
        except ProviderUnavailable:
            self.record("provider_transport", "rejected", ("provider_unavailable",))
            self.check_failed(("provider_unavailable", "provider_transport"))
        except ProviderRejected:
            self.record("provider_transport", "rejected", ("provider_rejected",))
            self.check_failed(("provider_rejected", "provider_transport"))
        except ValidationError:
            code = (
                "unparsable_generation_outcome"
                if mode == "default_ir"
                else "unparsable_fallback_ir"
            )
            self.record(stage, "rejected", (code,))
            self.check_failed((code, stage))

        for _ in generation.transport_attempts:
            self.budget.record_transport_attempt()
        self.budget.record_semantic_call(
            input_tokens=generation.usage.input_tokens,
            output_tokens=generation.usage.output_tokens,
            cost_usd=generation.usage.cost_usd,
        )
        self.record(stage, "accepted")
        return generation.output

    def _generate(self):
        self.route = "default_ir"
        outcome = self._call_provider("default_ir", TypeAdapter(IRGenerationOutcome))

        if isinstance(outcome, GroundingRefusal):
            decision = check_grounding_refusal(outcome, self.snapshot)
            if decision.violation is not None:
                self.record("snapshot", "rejected", (decision.violation.code,))
                self.check_failed_with((decision.violation,))
            self.record("snapshot", "accepted")
            self.refuse("missing_grounding", unmet_needs=outcome.unmet_needs)

        if isinstance(outcome, ClarificationRequest):
            decision = validate_clarification(
                outcome, self.canonical_question, self.snapshot
            )
            if decision.violation is not None:
                self.record("clarification", "rejected", (decision.violation.code,))
                self.check_failed_with((decision.violation,))
            self.record("clarification", "accepted")
            self.refuse("clarification_required", ambiguities=decision.ambiguities)

        if isinstance(outcome, ComplexQueryPlan):
            self.budget.before("complexity")
            decision = self.agent._router.validate(outcome, self.snapshot)
            if decision.accepted is None:
                self.record("complexity", "rejected", ("unsupported_complexity",))
                self.refuse(
                    "unsupported_complexity",
                    unsupported_operator_ids=tuple(
                        operator
                        for operator in outcome.operator_ids
                        if operator
                        in {
                            "window.period_over_period.v1",
                            "set_operation.safe_binary.v1",
                        }
                    )
                    or ("window.period_over_period.v1",),
                )
            self.record("complexity", "accepted")
            try:
                self.budget.authorize_planned_ir(decision.accepted)
            except BudgetTransitionDenied:
                self.record("planned_ir", "rejected", ("budget_exceeded",))
                self.check_failed(("budget_exceeded", "planned_ir"))
            self.route = "planned_ir"
            fallback = self._call_provider(
                "planned_ir", TypeAdapter(RelationalQueryIR), decision.accepted
            )
            return self._validate_ir(fallback, decision.accepted.plan_hash)

        return self._validate_ir(outcome, None)

    # -- local gates ------------------------------------------------------
    def _validate_ir(self, ir, plan_hash):
        from .models import ValidatedIR

        result = validate_ir(ir, self.snapshot, self.canonical_question, self.route)
        if result.validated_ir is None:
            codes = tuple(violation.code for violation in result.violations)
            self.record(self.route, "rejected", codes)
            self.check_failed_with(result.violations, ir=ir)
        self.record(self.route, "accepted")
        return ValidatedIR(
            ir=result.validated_ir,
            ir_hash=relational_ir_sha256(result.validated_ir),
            snapshot_hash=self.snapshot.snapshot_hash,
            canonical_question_hash=self.question_hash,
            generation_route=self.route,
            accepted_complex_plan_hash=plan_hash,
        )

    def _finish(self, validated):
        agent = self.agent
        self.route = validated.generation_route
        self.grounding_usage = _usage_from_ir(validated.ir)

        # A cache hit re-enters validation with its original route.
        if self.cache_status == "hit":
            result = validate_ir(
                validated.ir, self.snapshot, self.canonical_question, self.route
            )
            if result.validated_ir is None:
                self.record(
                    self.route,
                    "rejected",
                    tuple(violation.code for violation in result.violations),
                )
                self.check_failed_with(result.violations, ir=validated.ir)
            self.record(self.route, "accepted")

        # 8. Compile deterministically. Resolved values stay internal.
        self.budget.before("literal_resolution")
        self.budget.before("compile")
        try:
            compiled = agent._compiler.compile(
                validated, self.snapshot, self.canonical_question, self.request.max_rows
            )
        except CompilerError as error:
            stage = (
                "literal_resolution"
                if error.code.startswith(("invalid_literal", "ungrounded_literal"))
                else "compile"
            )
            self.record(stage, "rejected", (error.code,))
            if stage == "literal_resolution":
                # A literal that cannot resolve is a local clarification need,
                # never a fabricated ambiguity.
                self.refuse(
                    "clarification_required",
                    literal_needs=(
                        LiteralClarificationNeed(
                            kind="literal_need",
                            issue=_literal_issue(error.code),
                            expected_type="string",
                        ),
                    ),
                )
            self.check_failed((error.code, "compile"), ir=validated.ir)
        self.record("compile", "accepted")

        # 9. Authorize the compiled AST against the accepted IR.
        self.budget.before("ast_check")
        authorization = authorize_compiled_query(
            compiled, validated, self.snapshot, self.canonical_question, agent._caps
        )
        if authorization.violations:
            codes = tuple(violation.code for violation in authorization.violations)
            self.record("ast_check", "rejected", codes)
            if "unbounded_sensitive_projection" in codes:
                self.refuse(
                    "policy_disallowed",
                    policy_ids=tuple(sorted(self.snapshot.policy_ids))
                    or ("policy.sensitive-output",),
                )
            self.check_failed_with(
                authorization.violations,
                ir=validated.ir,
            )
        self.record("ast_check", "accepted")
        artifact = to_sql_artifact(compiled)

        # 10. Mandatory parameter-aware EXPLAIN.
        self.budget.before("engine_validation")
        engine_violations = agent._validator.validate(compiled)
        if engine_violations:
            codes = tuple(violation.code for violation in engine_violations)
            self.record("engine_validation", "rejected", codes)
            self.check_failed_with(tuple(engine_violations), ir=validated.ir)
        self.record("engine_validation", "accepted")

        # 11. Execute once under the remaining deadline.
        self.budget.before("execution")
        try:
            result = agent._executor.execute(compiled, self.request.max_rows)
        except TimeoutError:
            self.record("execution", "rejected", ("execution_timeout",))
            self.check_failed(("execution_timeout", "execution"), ir=validated.ir)
        except Exception:  # noqa: BLE001 - sanitized to a stable code
            self.record("execution", "rejected", ("execution_error",))
            self.check_failed(("execution_error", "execution"), ir=validated.ir)
        self.record("execution", "accepted")

        # 12. Cache only validated, value-free IR, then assemble one response.
        if self.cache_status == "miss":
            agent._cache.put(
                self._cache_key(self.route),
                CachedGeneration(
                    ir=validated.ir,
                    generation_route=self.route,
                    accepted_complex_plan_hash=validated.accepted_complex_plan_hash,
                    payload_sha256=cache_payload_sha256(
                        ir=validated.ir,
                        generation_route=self.route,
                        accepted_complex_plan_hash=validated.accepted_complex_plan_hash,
                    ),
                ),
            )
        return OkResponse(
            **{**self._base_fields(), "status": "ok"},
            ir=validated.ir,
            sql_artifact=artifact,
            result=result,
            output_lineage=authorization.output_lineage,
            disclosures=authorization.disclosures,
        )


def _literal_issue(code: str) -> str:
    if code == "ungrounded_literal_reference":
        return "ungrounded_governed_literal"
    if code == "invalid_literal_type":
        return "literal_type_mismatch"
    if code == "invalid_literal_bound":
        return "unparseable_question_literal"
    return "invalid_question_span"


def _usage_from_ir(ir: Any) -> GroundingUsage:
    objects: set[str] = set()
    relationships: set[str] = set()
    literals: set[str] = set()
    for node in ir.nodes:
        if node.kind == "scan":
            objects.add(node.table_id)
        if node.kind == "join":
            relationships.add(node.relationship_id)
    for node in ir.nodes:
        for attribute in ("group_by", "measures", "outputs"):
            for item in getattr(node, attribute, ()) or ():
                expression = getattr(item, "expression", None)
                if getattr(expression, "kind", None) == "metric":
                    objects.add(expression.metric_id)
    return GroundingUsage(
        object_ids=frozenset(objects),
        relationship_ids=frozenset(relationships),
        governed_literal_ids=frozenset(literals),
    )
