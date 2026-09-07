from __future__ import annotations

import inspect
import json
from decimal import Decimal

import pytest
import text2sql_factories as factories
from pydantic import TypeAdapter, ValidationError

import cerebro.hosted_provider
from cerebro.hosted_provider import GuardedProvider, ScriptedProvider
from cerebro.models import (
    BoundParameter,
    CheckViolation,
    ComplexQueryPlan,
    GuardedGenerationRequest,
    IRGenerationOutcome,
    LiteralExpression,
    QueryResult,
    RelationalQueryIR,
    SnapshotGovernedLiteral,
    complex_plan_sha256,
)
from cerebro.prompting import (
    PromptEnvelope,
    ProviderProbe,
    prompt_snapshot_view,
)
from cerebro.text2sql_provider import (
    EgressBlocked,
    ProviderGeneration,
    Text2SQLGenerationProvider,
)

# Canaries live only in local structures. None may reach the rendered prompt.
SOURCE_ROW_CANARY = "CANARY_SOURCE_ROW_Nguyen_Van_A"
NUMERIC_PII_CANARY = "4111111111111111"
DISCOVERED_VALUE_CANARY = "CANARY_DISCOVERED_SAMPLE"
RESOLVED_LITERAL_CANARY = "CANARY_RESOLVED_LONDON"
RESULT_CANARY = "CANARY_RESULT_CELL"
CREDENTIAL_CANARY = "sk-CANARY-secret-key"
RAW_SQL_CANARY = "SELECT CANARY FROM accounts"
RAW_EXCEPTION_CANARY = "Traceback CANARY_EXCEPTION at line 42"
FREE_TEXT_SUBJECT_CANARY = "CANARY_FREE_TEXT_SUBJECT"

ALL_CANARIES = (
    SOURCE_ROW_CANARY,
    NUMERIC_PII_CANARY,
    DISCOVERED_VALUE_CANARY,
    RESOLVED_LITERAL_CANARY,
    RESULT_CANARY,
    CREDENTIAL_CANARY,
    RAW_SQL_CANARY,
    RAW_EXCEPTION_CANARY,
    FREE_TEXT_SUBJECT_CANARY,
)


def _outcome_adapter() -> TypeAdapter:
    return TypeAdapter(IRGenerationOutcome)


def _request(mode: str = "default_ir", **overrides) -> GuardedGenerationRequest:
    snapshot = overrides.pop("snapshot", None) or factories.valid_snapshot()
    return GuardedGenerationRequest(
        mode=mode,
        canonical_question=overrides.pop(
            "canonical_question", factories.canonical_question()
        ),
        snapshot=snapshot,
        accepted_complex_route=overrides.pop("accepted_complex_route", None),
        prior_violations=overrides.pop("prior_violations", ()),
    )


def _scripted(outcomes=None) -> ScriptedProvider:
    return ScriptedProvider(outcomes if outcomes is not None else [])


# --- the boundary accepts only a locally built request ----------------------


@pytest.mark.parametrize(
    "forged",
    [
        {"canonical_question": "q"},
        "rendered prompt",
        ["canonical_question", "q"],
        42,
        None,
    ],
)
def test_guard_rejects_caller_built_payloads(forged):
    inner = _scripted()
    guarded = GuardedProvider(inner)
    with pytest.raises(EgressBlocked):
        guarded.generate(forged, _outcome_adapter())
    assert inner.calls == []


def test_guard_rejects_a_prebuilt_prompt_envelope():
    inner = _scripted()
    guarded = GuardedProvider(inner)
    envelope = PromptEnvelope(
        mode="default_ir",
        canonical_question="q",
        snapshot=prompt_snapshot_view(factories.valid_snapshot()),
    )
    with pytest.raises(EgressBlocked):
        guarded.generate(envelope, _outcome_adapter())
    assert inner.calls == []


def test_guard_rejects_a_structurally_similar_foreign_request_object():
    class LookalikeRequest:
        mode = "default_ir"
        canonical_question = "q"
        snapshot = factories.valid_snapshot()
        accepted_complex_route = None
        prior_violations = ()

    inner = _scripted()
    guarded = GuardedProvider(inner)
    with pytest.raises(EgressBlocked):
        guarded.generate(LookalikeRequest(), _outcome_adapter())
    assert inner.calls == []


# --- provider schemas never ask for SQL or embedded values ------------------


def test_all_provider_output_schemas_exclude_sql_and_embedded_literal_values():
    for output_type in (IRGenerationOutcome, RelationalQueryIR, ProviderProbe):
        schema = json.dumps(TypeAdapter(output_type).json_schema(), sort_keys=True)
        assert '"sql"' not in schema
        assert '"raw_sql"' not in schema
    assert '"value"' not in json.dumps(
        LiteralExpression.model_json_schema(), sort_keys=True
    )
    assert '"intent"' not in json.dumps(
        RelationalQueryIR.model_json_schema(), sort_keys=True
    )


def test_text2sql_provider_protocol_is_consumer_owned():
    source = inspect.getsource(cerebro.hosted_provider)
    assert "cerebro.enrichment" not in source
    assert "from .enrichment" not in source
    assert isinstance(GuardedProvider(_scripted()), Text2SQLGenerationProvider)


def test_text2sql_modules_import_no_vendor_sdk_or_enrichment():
    import cerebro.prompting
    import cerebro.text2sql_provider

    for module in (
        cerebro.prompting,
        cerebro.text2sql_provider,
        cerebro.hosted_provider,
    ):
        source = inspect.getsource(module)
        for forbidden_import in (
            "from .enrichment",
            "from cerebro.enrichment",
            "import cerebro.enrichment",
            "import openai",
            "from openai",
        ):
            assert forbidden_import not in source, (
                f"{module.__name__}: {forbidden_import}"
            )


# --- canaries cannot enter the envelope ------------------------------------


def test_no_local_value_or_diagnostic_canary_reaches_the_prompt(monkeypatch):
    monkeypatch.setenv("CEREBRO_API_KEY", CREDENTIAL_CANARY)
    # Every canary below lives in a local-only structure that orchestration
    # holds at the same time as the request. None is an envelope input.
    local_parameter = BoundParameter(
        position=1, data_type="string", value=RESOLVED_LITERAL_CANARY
    )
    local_result = QueryResult(
        columns=("customer_name",),
        column_types=("string",),
        rows=((RESULT_CANARY,),),
        row_count=1,
        truncated=False,
        elapsed_ms=1,
    )
    local_notes = {
        "source_row": SOURCE_ROW_CANARY,
        "card": NUMERIC_PII_CANARY,
        "discovered": DISCOVERED_VALUE_CANARY,
        "sql": RAW_SQL_CANARY,
        "exception": RAW_EXCEPTION_CANARY,
        "free_text": FREE_TEXT_SUBJECT_CANARY,
    }
    assert local_parameter.value == RESOLVED_LITERAL_CANARY
    assert local_result.rows[0][0] == RESULT_CANARY
    assert local_notes["sql"] == RAW_SQL_CANARY

    inner = _scripted([factories.minimal_ir()])
    guarded = GuardedProvider(inner)
    guarded.generate(_request(), _outcome_adapter())

    assert len(inner.calls) == 1
    rendered = inner.calls[0]["prompt"]
    assert isinstance(rendered, str)
    for canary in ALL_CANARIES:
        assert canary not in rendered


def test_authored_governed_literal_is_allowed_but_a_lookalike_is_not():
    governed = SnapshotGovernedLiteral(
        literal_id="literal.open-status",
        data_type="string",
        value="OPEN",
        source_object_id="table.accounts",
    )
    snapshot = factories.valid_snapshot(governed_literals=(governed,))
    inner = _scripted([factories.minimal_ir()])
    GuardedProvider(inner).generate(_request(snapshot=snapshot), _outcome_adapter())
    rendered = inner.calls[0]["prompt"]
    assert "literal.open-status" in rendered
    assert "OPEN" in rendered
    # A value that no snapshot literal_id identifies must never appear, even
    # though it looks exactly like a governed constant.
    assert DISCOVERED_VALUE_CANARY not in rendered


def test_prompt_carries_only_allowlisted_snapshot_metadata():
    snapshot = factories.valid_snapshot()
    inner = _scripted([factories.minimal_ir()])
    GuardedProvider(inner).generate(_request(snapshot=snapshot), _outcome_adapter())
    payload = json.loads(inner.calls[0]["prompt"])
    assert set(payload) == {
        "mode",
        "canonical_question",
        "snapshot",
        "accepted_complex_plan",
        "violations",
    }
    assert payload["canonical_question"] == factories.canonical_question()
    assert payload["accepted_complex_plan"] is None
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "snapshot_hash",
        "authorization_scope_hash",
        "retrieval_config_hash",
        "provenance",
        "body",
    ):
        assert forbidden not in serialized


def test_violation_subjects_are_sanitized_to_codes_and_a_phase_token():
    violation = CheckViolation(
        code="non_boolean_filter",
        stage="ast_check",
        subject_ids=("filter_accounts",),
    )
    inner = _scripted([factories.minimal_ir()])
    GuardedProvider(inner).generate(
        _request(prior_violations=(violation,)), _outcome_adapter()
    )
    payload = json.loads(inner.calls[0]["prompt"])
    assert payload["violations"] == [
        {
            "code": "non_boolean_filter",
            "phase": "ast_check",
            "remediation": payload["violations"][0]["remediation"],
        }
    ]
    assert payload["violations"][0]["remediation"]
    assert "filter_accounts" not in json.dumps(payload, sort_keys=True)


def test_nonmember_violation_subject_is_blocked_with_zero_calls():
    violation = CheckViolation(
        code="unknown_table",
        stage="ast_check",
        subject_ids=("table.atms",),
    )
    inner = _scripted([factories.minimal_ir()])
    guarded = GuardedProvider(inner)
    with pytest.raises(EgressBlocked):
        guarded.generate(_request(prior_violations=(violation,)), _outcome_adapter())
    assert inner.calls == []


def test_snapshot_with_nonmember_relationship_endpoint_is_blocked():
    snapshot = factories.valid_snapshot()
    # Keep one relationship whose right endpoint table is absent from the
    # snapshot objects, so the envelope would otherwise leak its existence.
    accounts = next(
        item for item in snapshot.objects if item.object_id == "table.accounts"
    )
    broken = snapshot.model_copy(
        update={
            "objects": (accounts,),
            "authorized_object_ids": frozenset({"table.accounts"}),
        }
    )
    inner = _scripted([factories.minimal_ir()])
    guarded = GuardedProvider(inner)
    with pytest.raises(EgressBlocked):
        guarded.generate(_request(snapshot=broken), _outcome_adapter())
    assert inner.calls == []


# --- planned route authority -----------------------------------------------


def _planned_request():
    snapshot = factories.valid_snapshot()
    plan = factories.complex_window_plan(snapshot)
    from cerebro.models import _create_accepted_complex_route

    route = _create_accepted_complex_route(
        snapshot_hash=snapshot.snapshot_hash,
        plan_hash=complex_plan_sha256(plan),
        plan=plan,
    )
    return (
        GuardedGenerationRequest(
            mode="planned_ir",
            canonical_question=factories.canonical_question(),
            snapshot=snapshot,
            accepted_complex_route=route,
            prior_violations=(),
        ),
        route,
        plan,
    )


def test_planned_request_renders_only_the_accepted_plan():
    request, _, plan = _planned_request()
    inner = _scripted([factories.minimal_ir()])
    GuardedProvider(inner).generate(request, TypeAdapter(RelationalQueryIR))
    payload = json.loads(inner.calls[0]["prompt"])
    assert payload["mode"] == "planned_ir"
    assert payload["accepted_complex_plan"] == json.loads(plan.model_dump_json())


def test_post_validation_route_mutation_is_rejected_before_egress():
    request, route, _ = _planned_request()
    other_plan = ComplexQueryPlan.model_validate(
        {
            **json.loads(route.plan.model_dump_json()),
            "expected_outputs": ("exfiltrated",),
        }
    )
    # Simulate a mutation that slipped past construction-time validation.
    object.__setattr__(route, "plan", other_plan)
    inner = _scripted([factories.minimal_ir()])
    guarded = GuardedProvider(inner)
    with pytest.raises(EgressBlocked):
        guarded.generate(request, TypeAdapter(RelationalQueryIR))
    assert inner.calls == []


def test_default_mode_never_sends_a_plan_and_planned_mode_requires_one():
    inner = _scripted([factories.minimal_ir()])
    GuardedProvider(inner).generate(_request(), _outcome_adapter())
    assert json.loads(inner.calls[0]["prompt"])["accepted_complex_plan"] is None


# --- outputs stay schema bound --------------------------------------------


def test_guarded_provider_returns_validated_provider_generation():
    outcome = factories.minimal_ir()
    inner = _scripted([outcome])
    generation = GuardedProvider(inner).generate(_request(), _outcome_adapter())
    assert isinstance(generation, ProviderGeneration)
    assert generation.output == outcome
    assert generation.transport_attempts
    assert generation.usage.input_tokens >= 0
    assert isinstance(generation.usage.cost_usd, Decimal)


def test_provider_output_that_is_not_schema_valid_fails_as_a_semantic_error():
    inner = _scripted([{"outcome": "ir", "ir_version": "008.ir.v1"}])
    with pytest.raises(ValidationError):
        GuardedProvider(inner).generate(_request(), _outcome_adapter())


# --- the offline reference provider honours the same boundary ---------------


def test_golden_provider_speaks_only_the_guarded_protocol():
    """The offline reference goes through the same boundary as the organizer."""
    import golden_answers

    provider = golden_answers.GoldenProvider()
    assert isinstance(provider, Text2SQLGenerationProvider)
    for attribute in ("provider", "model", "model_revision", "schema_mechanism"):
        assert isinstance(getattr(provider, attribute), str)


def test_golden_provider_rejects_an_unguarded_payload():
    import golden_answers

    from cerebro.text2sql_provider import ProviderRejected

    provider = golden_answers.GoldenProvider()
    with pytest.raises(ProviderRejected):
        provider.generate({"mode": "default_ir"}, _outcome_adapter())


def test_golden_answers_module_never_imports_enrichment():
    """Query generation and bundle enrichment stay separate workstreams."""
    import ast

    import golden_answers

    tree = ast.parse(inspect.getsource(golden_answers))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not {name for name in imported if "enrichment" in name}


def test_golden_answers_stores_no_sql_or_resolved_value():
    import golden_answers

    source = inspect.getsource(golden_answers)
    for forbidden in ("SELECT ", "BoundParameter", "ResolvedLiteral"):
        assert forbidden not in source
