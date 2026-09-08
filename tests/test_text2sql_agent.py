from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import duckdb
import pytest
import text2sql_factories as factories

from cerebro.complexity import ComplexityRouter
from cerebro.executor import DuckDBExecutor
from cerebro.models import (
    BudgetLimits,
    GroundingRefusal,
    SQLGenerationRequest,
    TableGroundingNeed,
)
from cerebro.provenance import canonical_question_sha256, canonicalize_question
from cerebro.selfcheck import DisclosureCaps
from cerebro.sql_compiler import DialectCompiler
from cerebro.text2sql import RuntimeVersions, Text2SQLAgent
from cerebro.text2sql_cache import CacheKey, Text2SQLCache, cache_payload_sha256
from cerebro.text2sql_provider import (
    ProviderGeneration,
    ProviderUsage,
    TransportAttempt,
)


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    """A schema matching the shared factory snapshot, not the real corpus."""
    path = tmp_path_factory.mktemp("agent") / "agent.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE accounts ("
        "account_id BIGINT, branch_id BIGINT, city VARCHAR, "
        "customer_name VARCHAR, status VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE branches (branch_id BIGINT, branch_name VARCHAR, city VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE transactions ("
        "transaction_id BIGINT, account_id BIGINT, amount DOUBLE, "
        "txn_date DATE, txn_type VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
        [
            (i, i % 3, "London" if i % 2 else "Delhi", f"Name {i}", "OPEN")
            for i in range(1, 13)
        ],
    )
    connection.executemany(
        "INSERT INTO branches VALUES (?, ?, ?)",
        [(i, f"Branch {i}", "London") for i in range(3)],
    )
    connection.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?)",
        [(i, i % 12 + 1, 10.0 * i, "2026-01-01", "Deposit") for i in range(1, 25)],
    )
    connection.close()
    return str(path)


class FakeResolver:
    """Explicit test composition: retrieval is exercised in its own suite."""

    def __init__(self, snapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    def resolve(self, canonical_question, scope, dialect):
        self.calls.append(canonical_question)
        return self._snapshot


class FakeProvider:
    """A protocol-conforming provider that counts semantic calls exactly."""

    provider = "scripted"
    model = "scripted-model"
    model_revision = "scripted"
    schema_mechanism = "json_schema"

    def __init__(self, outputs) -> None:
        self._outputs = list(outputs)
        self.semantic_calls = 0
        self.modes: list[str] = []

    def generate(self, request, output_adapter):
        self.semantic_calls += 1
        self.modes.append(request.mode)
        if not self._outputs:
            raise AssertionError("provider called more times than the test scripted")
        payload = self._outputs.pop(0)
        output = output_adapter.validate_python(
            payload
            if isinstance(payload, (dict, list))
            else payload.model_dump(mode="json")
        )
        return ProviderGeneration(
            output=output,
            transport_attempts=(
                TransportAttempt(ordinal=1, outcome="accepted", latency_ms=1),
            ),
            usage=ProviderUsage(
                input_tokens=10, output_tokens=5, cost_usd=Decimal("0.001")
            ),
        )


class CountingEngine:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.validate_calls = 0
        self.execute_calls = 0

    def validate(self, compiled):
        self.validate_calls += 1
        return self._inner.validate(compiled)

    def execute(self, compiled, max_rows):
        self.execute_calls += 1
        return self._inner.execute(compiled, max_rows)


@dataclass
class Fixture:
    agent: Text2SQLAgent
    provider: FakeProvider
    engine: CountingEngine
    cache: Text2SQLCache
    resolver: FakeResolver
    snapshot: object = None
    versions: object = None
    scripted: list = field(default_factory=list)


@pytest.fixture()
def agent_fixture(snapshot, database):
    engines: list[DuckDBExecutor] = []

    def build(provider_outputs=(), *, preloaded=None, caps=None):
        provider = FakeProvider(provider_outputs)
        inner = DuckDBExecutor(database)
        engines.append(inner)
        engine = CountingEngine(inner)
        cache = Text2SQLCache()
        resolver = FakeResolver(snapshot)
        versions = RuntimeVersions(
            semantic_version=snapshot.semantic_version,
            policy_version=snapshot.policy_version,
            prompt_version="008.prompt.v1",
            router_version="008.router.v1",
            checker_version="008.checker.v1",
        )
        agent = Text2SQLAgent(
            resolver=resolver,
            provider=provider,
            router=ComplexityRouter(),
            compiler=DialectCompiler("duckdb"),
            cache=cache,
            validator=engine,
            executor=engine,
            disclosure_caps=caps or DisclosureCaps.defaults(),
            budget_limits=BudgetLimits.defaults(),
            versions=versions,
        )
        if preloaded is not None:
            route, ir, question = preloaded
            key = CacheKey(
                authorization_scope_hash=snapshot.authorization_scope_hash,
                snapshot_hash=snapshot.snapshot_hash,
                policy_version=snapshot.policy_version,
                canonicalization_version="008.question.v1",
                canonical_question_hash=canonical_question_sha256(
                    canonicalize_question(question)
                ),
                literal_registry_version="008.literal-span.v1",
                dialect="duckdb",
                generation_route=route,
                provider=provider.provider,
                model=provider.model,
                model_revision=provider.model_revision,
                schema_mechanism=provider.schema_mechanism,
                prompt_version=versions.prompt_version,
                ir_contract_version="008.ir.v1",
                router_version=versions.router_version,
                compiler_version=agent.compiler_version,
                type_registry_version="008.types.v1",
                checker_version=versions.checker_version,
            )
            from cerebro.models import CachedGeneration, complex_plan_sha256

            plan_hash = (
                complex_plan_sha256(factories.complex_window_plan(snapshot))
                if route == "planned_ir"
                else None
            )
            cache.put(
                key,
                CachedGeneration(
                    ir=ir,
                    generation_route=route,
                    accepted_complex_plan_hash=plan_hash,
                    payload_sha256=cache_payload_sha256(
                        ir=ir,
                        generation_route=route,
                        accepted_complex_plan_hash=plan_hash,
                    ),
                ),
            )
        return Fixture(
            agent=agent,
            provider=provider,
            engine=engine,
            cache=cache,
            resolver=resolver,
            snapshot=snapshot,
            versions=versions,
        )

    yield build
    for engine in engines:
        engine.close()


def _request(question="Show accounts in London", scope=None):
    return SQLGenerationRequest(
        question=question,
        authorization_scope=scope or factories.valid_scope(),
        dialect="duckdb",
        max_rows=100,
    )


# --- call counts -----------------------------------------------------------


def test_normal_request_makes_one_semantic_call(agent_fixture, snapshot):
    fixture = agent_fixture([factories.filtered_account_ir(snapshot)])
    response = fixture.agent.run(_request())
    assert response.status == "ok", response.violations
    assert response.generation_route == "default_ir"
    assert response.cache_status == "miss"
    assert fixture.provider.semantic_calls == 1
    assert response.budget_usage.semantic_calls == 1
    assert fixture.engine.validate_calls == 1
    assert fixture.engine.execute_calls == 1


def test_ok_response_is_value_free_and_carries_full_provenance(agent_fixture, snapshot):
    fixture = agent_fixture([factories.filtered_account_ir(snapshot)])
    response = fixture.agent.run(_request())
    assert response.status == "ok"
    serialized = response.model_dump_json()
    assert "London" not in serialized
    assert response.snapshot_hash == snapshot.snapshot_hash
    assert response.canonical_question_hash == canonical_question_sha256(
        canonicalize_question("Show accounts in London")
    )
    assert response.sql_artifact.parameter_count >= 1
    assert response.result.row_count >= 0
    assert response.output_lineage
    assert response.contract_version == "008.v3"


def test_complex_request_makes_exactly_two_semantic_calls_after_transition(
    agent_fixture, snapshot
):
    fixture = agent_fixture(
        [
            factories.complex_window_plan(snapshot),
            factories.complex_node_ir(snapshot, "window"),
        ]
    )
    response = fixture.agent.run(_request(question="accounts"))
    assert response.status == "ok", response.violations
    assert response.generation_route == "planned_ir"
    assert response.cache_status == "miss"
    assert response.budget_usage.semantic_calls == 2
    assert response.budget_usage.planned_ir_authorized is True
    assert fixture.provider.modes == ["default_ir", "planned_ir"]


def test_cached_planned_ir_preserves_generation_route_and_revalidates(
    agent_fixture, snapshot
):
    fixture = agent_fixture(
        [],
        preloaded=(
            "planned_ir",
            factories.complex_node_ir(snapshot, "window"),
            "accounts",
        ),
    )
    response = fixture.agent.run(_request(question="accounts"))
    assert response.status == "ok", response.violations
    assert response.generation_route == "planned_ir"
    assert response.cache_status == "hit"
    assert fixture.provider.semantic_calls == 0
    assert fixture.engine.validate_calls == 1
    assert fixture.engine.execute_calls == 1


def test_cached_default_ir_hit_makes_no_semantic_call(agent_fixture, snapshot):
    fixture = agent_fixture(
        [],
        preloaded=(
            "default_ir",
            factories.filtered_account_ir(snapshot),
            "Show accounts in London",
        ),
    )
    response = fixture.agent.run(_request())
    assert response.status == "ok", response.violations
    assert response.cache_status == "hit"
    assert response.generation_route == "default_ir"
    assert fixture.provider.semantic_calls == 0


def test_successful_miss_populates_the_cache_with_value_free_ir(
    agent_fixture, snapshot
):
    fixture = agent_fixture([factories.filtered_account_ir(snapshot)])
    fixture.agent.run(_request())
    stored = list(fixture.cache._entries.values())
    assert len(stored) == 1
    _, value = stored[0]
    assert value.generation_route == "default_ir"
    assert "London" not in value.model_dump_json()


# --- local refusals --------------------------------------------------------


def test_absent_grounding_need_is_a_local_refusal(agent_fixture, snapshot):
    refusal = GroundingRefusal(
        outcome="grounding_refusal",
        unmet_needs=(TableGroundingNeed(kind="table", object_id="table.atms"),),
    )
    fixture = agent_fixture([refusal])
    response = fixture.agent.run(_request(question="how many atms are there"))
    assert response.status == "refused"
    assert response.reason == "missing_grounding"
    assert fixture.engine.validate_calls == 0
    assert fixture.engine.execute_calls == 0
    assert fixture.provider.semantic_calls == 1


def test_false_missing_grounding_claim_is_check_failed(agent_fixture, snapshot):
    refusal = GroundingRefusal(
        outcome="grounding_refusal",
        unmet_needs=(TableGroundingNeed(kind="table", object_id="table.accounts"),),
    )
    fixture = agent_fixture([refusal])
    response = fixture.agent.run(_request())
    assert response.status == "check_failed"
    assert "false_missing_grounding" in {v.code for v in response.violations}
    assert fixture.engine.validate_calls == 0


def test_valid_clarification_terminates_without_fallback_or_engine(
    agent_fixture, snapshot
):
    question = "volume by customer or account"
    canonical = canonicalize_question(question)
    from cerebro.models import Ambiguity, ClarificationRequest, ObjectAmbiguityCandidate

    start = canonical.index("customer")
    request = ClarificationRequest(
        outcome="clarification_request",
        ambiguities=(
            Ambiguity(
                ambiguity_id="target_object",
                start=start,
                end=start + len("customer"),
                candidates=(
                    ObjectAmbiguityCandidate(kind="object", object_id="table.accounts"),
                    ObjectAmbiguityCandidate(
                        kind="object", object_id="table.transactions"
                    ),
                ),
            ),
        ),
    )
    fixture = agent_fixture([request])
    response = fixture.agent.run(_request(question=question))
    assert response.status == "refused"
    assert response.reason == "clarification_required"
    assert response.generation_route == "default_ir"
    assert response.cache_status == "miss"
    assert response.ambiguities
    assert fixture.provider.semantic_calls == 1
    assert fixture.engine.validate_calls == 0
    assert fixture.engine.execute_calls == 0


def test_invalid_clarification_is_check_failed(agent_fixture, snapshot):
    from cerebro.models import Ambiguity, ClarificationRequest, ObjectAmbiguityCandidate

    request = ClarificationRequest(
        outcome="clarification_request",
        ambiguities=(
            Ambiguity(
                ambiguity_id="target_object",
                start=0,
                end=2,
                candidates=(
                    ObjectAmbiguityCandidate(kind="object", object_id="table.accounts"),
                    ObjectAmbiguityCandidate(
                        kind="object", object_id="policy.sensitive-output"
                    ),
                ),
            ),
        ),
    )
    fixture = agent_fixture([request])
    response = fixture.agent.run(_request())
    assert response.status == "check_failed"
    assert "invalid_clarification_request" in {v.code for v in response.violations}
    assert fixture.engine.validate_calls == 0


def test_unsupported_complex_plan_is_refused_before_compilation(
    agent_fixture, snapshot
):
    """An operator outside the allowlist cannot even be schema-valid, so the
    router's refusal path is reached through an ungrounded decomposition."""
    plan = factories.complex_window_plan(snapshot)
    ungrounded = plan.model_copy(
        update={
            "steps": (
                plan.steps[0].model_copy(update={"input_object_ids": ("table.atms",)}),
            )
        }
    )
    fixture = agent_fixture([ungrounded])
    response = fixture.agent.run(_request(question="accounts"))
    assert response.status == "refused"
    assert response.reason == "unsupported_complexity"
    assert fixture.provider.semantic_calls == 1
    assert fixture.engine.validate_calls == 0


def test_operator_outside_the_allowlist_fails_schema_decoding(agent_fixture, snapshot):
    plan = factories.complex_window_plan(snapshot).model_dump(mode="python")
    plan["operator_ids"] = ("window.period_over_period.v1", "recursive_query.v1")
    fixture = agent_fixture([plan])
    response = fixture.agent.run(_request(question="accounts"))
    assert response.status == "check_failed"
    assert "unparsable_generation_outcome" in {v.code for v in response.violations}
    assert fixture.engine.validate_calls == 0


def test_unbounded_sensitive_output_is_local_policy_disallowed(agent_fixture, snapshot):
    from cerebro.models import (
        ColumnExpression,
        ColumnRef,
        NamedExpression,
        ProjectNode,
        RelationalQueryIR,
        ScanNode,
    )

    ir = RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="project_names",
        nodes=(
            ScanNode(kind="scan", node_id="scan_accounts", table_id="table.accounts"),
            ProjectNode(
                kind="project",
                node_id="project_names",
                input_id="scan_accounts",
                outputs=(
                    NamedExpression(
                        alias="customer_name",
                        expression=ColumnExpression(
                            kind="column",
                            ref=ColumnRef(
                                table_id="table.accounts", column="customer_name"
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    fixture = agent_fixture([ir])
    response = fixture.agent.run(_request(question="all customer names"))
    assert response.status == "refused"
    assert response.reason == "policy_disallowed"
    assert fixture.engine.execute_calls == 0


def test_ungrounded_literal_ref_becomes_local_literal_clarification(
    agent_fixture, snapshot
):
    from cerebro.models import GovernedLiteralRef

    ir = factories.filtered_account_ir(
        snapshot,
        city_ref=GovernedLiteralRef(kind="governed", literal_id="literal.invented"),
    )
    fixture = agent_fixture([ir])
    response = fixture.agent.run(_request())
    assert response.status in {"refused", "check_failed"}
    if response.status == "refused":
        assert response.reason == "clarification_required"
        assert response.literal_needs
        assert not response.ambiguities
    assert fixture.engine.validate_calls == 0
    assert fixture.engine.execute_calls == 0


def test_invalid_ir_graph_is_check_failed_without_engine_contact(
    agent_fixture, snapshot
):
    ir = factories.filtered_account_ir(snapshot).model_copy(
        update={"root_node_id": "absent_node"}
    )
    fixture = agent_fixture([ir])
    response = fixture.agent.run(_request())
    assert response.status == "check_failed"
    assert "invalid_ir_root" in {v.code for v in response.violations}
    assert fixture.engine.validate_calls == 0
    assert fixture.provider.semantic_calls == 1


# --- budget and no-repair --------------------------------------------------


def test_no_path_makes_a_third_semantic_call(agent_fixture, snapshot):
    fixture = agent_fixture(
        [
            factories.complex_window_plan(snapshot),
            factories.complex_node_ir(snapshot, "window"),
        ]
    )
    response = fixture.agent.run(_request(question="accounts"))
    assert response.budget_usage.semantic_calls == 2
    assert fixture.provider.semantic_calls == 2


def test_engine_failure_does_not_trigger_provider_recall(agent_fixture, snapshot):
    class BrokenEngine:
        def __init__(self) -> None:
            self.validate_calls = 0
            self.execute_calls = 0

        def validate(self, compiled):
            self.validate_calls += 1
            from cerebro.models import CheckViolation

            return (
                CheckViolation(
                    code="explain_failed", stage="engine_validation", subject_ids=()
                ),
            )

        def execute(self, compiled, max_rows):  # pragma: no cover - never reached
            self.execute_calls += 1
            raise AssertionError("execution must not run after explain_failed")

    fixture = agent_fixture([factories.filtered_account_ir(snapshot)])
    broken = BrokenEngine()
    fixture.agent._validator = broken
    fixture.agent._executor = broken
    response = fixture.agent.run(_request())
    assert response.status == "check_failed"
    assert "explain_failed" in {v.code for v in response.violations}
    assert broken.execute_calls == 0
    assert fixture.provider.semantic_calls == 1


def test_attempt_records_name_every_stage_reached(agent_fixture, snapshot):
    fixture = agent_fixture([factories.filtered_account_ir(snapshot)])
    response = fixture.agent.run(_request())
    stages = [record.stage for record in response.attempt_records]
    assert "snapshot" in stages
    assert "default_ir" in stages
    assert "compile" in stages
    assert "engine_validation" in stages
    assert "execution" in stages
    assert [record.ordinal for record in response.attempt_records] == list(
        range(1, len(response.attempt_records) + 1)
    )


def test_agent_requires_every_dependency(snapshot, database):
    inner = DuckDBExecutor(database)
    try:
        with pytest.raises((TypeError, ValueError)):
            Text2SQLAgent(
                resolver=None,
                provider=FakeProvider([]),
                router=ComplexityRouter(),
                compiler=DialectCompiler("duckdb"),
                cache=Text2SQLCache(),
                validator=inner,
                executor=inner,
                disclosure_caps=DisclosureCaps.defaults(),
                budget_limits=BudgetLimits.defaults(),
                versions=RuntimeVersions(
                    semantic_version="v",
                    policy_version="p",
                    prompt_version="pr",
                    router_version="r",
                    checker_version="c",
                ),
            )
    finally:
        inner.close()


def test_orchestration_imports_no_enrichment_or_gateway():
    import inspect

    import cerebro.text2sql as orchestration

    source = inspect.getsource(orchestration)
    for forbidden in (
        "from .enrichment",
        "from cerebro.enrichment",
        "OrganizerModelGateway",
    ):
        assert forbidden not in source
