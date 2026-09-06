from __future__ import annotations

import json
import socket
from decimal import Decimal

import httpx
import pytest
import text2sql_factories as factories
from pydantic import TypeAdapter

from cerebro.hosted_provider import (
    CassetteProvider,
    GuardedProvider,
    OrganizerModelGateway,
    ScriptedProvider,
    install_offline_network_guard,
    probe_provider_schema,
)
from cerebro.models import (
    IRGenerationOutcome,
    ProviderCapabilityReceipt,
    RelationalQueryIR,
)
from cerebro.text2sql_provider import (
    ProviderConfigurationError,
    ProviderRejected,
    ProviderUnavailable,
    Text2SQLGenerationProvider,
)

SECRET = "sk-secret-do-not-leak"
BASE_ENVIRONMENT = {
    "CEREBRO_BASE_URL": "https://organizer.invalid/v1",
    "CEREBRO_API_KEY": SECRET,
    "CEREBRO_MODEL": "organizer-model",
    "CEREBRO_MODEL_REVISION": "2026-08-27",
}


def _outcome_payload() -> dict:
    return json.loads(factories.minimal_ir().model_dump_json())


def _gateway(handler, *, environment=None, **kwargs) -> OrganizerModelGateway:
    return OrganizerModelGateway.from_environment(
        environment if environment is not None else BASE_ENVIRONMENT,
        transport=httpx.MockTransport(handler),
        backoff_seconds=0.0,
        **kwargs,
    )


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(_outcome_payload())}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            "schema_mechanism": "json_schema",
        },
    )


# --- configuration ---------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["CEREBRO_BASE_URL", "CEREBRO_API_KEY", "CEREBRO_MODEL"]
)
def test_missing_configuration_is_a_typed_configuration_error(missing):
    environment = {
        key: value for key, value in BASE_ENVIRONMENT.items() if key != missing
    }
    with pytest.raises(ProviderConfigurationError):
        _gateway(_ok_response, environment=environment)


def test_configuration_error_message_never_contains_the_credential():
    environment = {**BASE_ENVIRONMENT, "CEREBRO_MODEL": ""}
    with pytest.raises(ProviderConfigurationError) as error:
        _gateway(_ok_response, environment=environment)
    assert SECRET not in str(error.value)


def test_runtime_identity_comes_from_the_environment():
    gateway = _gateway(_ok_response)
    assert gateway.model == "organizer-model"
    assert gateway.model_revision == "2026-08-27"
    assert gateway.provider == "organizer.invalid"
    assert SECRET not in repr(gateway)


# --- transport attempts versus semantic calls -------------------------------


def test_one_semantic_call_uses_one_transport_attempt_on_success():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _ok_response(request)

    generation = _gateway(handler).generate(
        "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
    )
    assert len(calls) == 1
    assert len(generation.transport_attempts) == 1
    assert generation.transport_attempts[0].outcome == "accepted"
    assert generation.usage.input_tokens == 11
    assert generation.usage.output_tokens == 7


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_faults_stop_at_exactly_two_transport_attempts(status):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": "retry later"})

    gateway = _gateway(handler)
    with pytest.raises(ProviderUnavailable) as error:
        gateway.generate("text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome))
    assert len(calls) == 2
    assert SECRET not in str(error.value)


def test_timeout_is_retryable_and_bounded():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).generate(
            "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
        )
    assert attempts["count"] == 2


def test_a_retry_can_succeed_within_the_same_semantic_call():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503, json={"error": "retry"})
        return _ok_response(request)

    generation = _gateway(handler).generate(
        "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
    )
    assert attempts["count"] == 2
    assert [item.outcome for item in generation.transport_attempts] == [
        "rejected",
        "accepted",
    ]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_rejection_never_becomes_provider_unavailable(status):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": "refused", "key": SECRET})

    gateway = _gateway(handler)
    with pytest.raises(ProviderRejected) as error:
        gateway.generate("text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome))
    assert len(calls) == 1
    assert SECRET not in str(error.value)


def test_schema_decode_failure_is_not_a_transport_fault():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"outcome": "ir"})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _gateway(handler).generate(
            "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
        )


def test_credential_travels_only_in_the_authorization_header():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["body"] = request.content.decode("utf-8")
        return _ok_response(request)

    generation = _gateway(handler).generate(
        "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
    )
    assert seen["auth"] == f"Bearer {SECRET}"
    assert SECRET not in seen["body"]
    for attempt in generation.transport_attempts:
        assert SECRET not in attempt.model_dump_json()


# --- capability probe ------------------------------------------------------


def test_probe_provider_schema_is_metadata_only_and_content_addressed(tmp_path):
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompts.append(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"ok": True})}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                "schema_mechanism": "json_schema",
            },
        )

    receipt, path = probe_provider_schema(
        gateway=_gateway(handler), receipt_dir=tmp_path
    )
    assert isinstance(receipt, ProviderCapabilityReceipt)
    assert receipt.model == "organizer-model"
    assert receipt.revision == "2026-08-27"
    assert receipt.schema_mechanism == "json_schema"
    assert len(prompts) == 1
    assert path.parent == tmp_path
    assert path.stem == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    written = ProviderCapabilityReceipt.model_validate_json(path.read_bytes())
    assert written == receipt
    serialized = path.read_text(encoding="utf-8")
    assert SECRET not in serialized
    assert "prompt" not in serialized
    assert "choices" not in serialized


def test_probe_uses_the_provider_probe_mode_and_an_empty_snapshot():
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"ok": True})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "schema_mechanism": "json_schema",
            },
        )

    probe_provider_schema(gateway=_gateway(handler), receipt_dir=None)
    prompt = json.loads(payloads[0]["messages"][-1]["content"])
    assert prompt["mode"] == "provider_probe"
    assert prompt["canonical_question"] == ""
    assert prompt["snapshot"]["objects"] == []
    assert prompt["snapshot"]["governed_literals"] == []


# --- adapters conform structurally -----------------------------------------


def test_scripted_and_cassette_adapters_conform_to_the_protocol(tmp_path):
    scripted = ScriptedProvider([factories.minimal_ir()])
    assert isinstance(scripted, Text2SQLGenerationProvider)

    cassette = tmp_path / "cassette.json"
    cassette.write_text(
        json.dumps({"default_ir": [_outcome_payload()]}), encoding="utf-8"
    )
    replay = CassetteProvider(cassette)
    assert isinstance(replay, Text2SQLGenerationProvider)
    generation = GuardedProvider(replay).generate(
        _default_request(), TypeAdapter(IRGenerationOutcome)
    )
    assert isinstance(generation.output, RelationalQueryIR)


def _default_request():
    from cerebro.models import GuardedGenerationRequest

    return GuardedGenerationRequest(
        mode="default_ir",
        canonical_question=factories.canonical_question(),
        snapshot=factories.valid_snapshot(),
    )


def test_missing_cassette_entry_fails_loudly_and_never_calls_live(tmp_path):
    cassette = tmp_path / "cassette.json"
    cassette.write_text(json.dumps({"default_ir": []}), encoding="utf-8")
    replay = CassetteProvider(cassette)
    with pytest.raises(ProviderUnavailable) as error:
        GuardedProvider(replay).generate(
            _default_request(), TypeAdapter(IRGenerationOutcome)
        )
    assert "cassette" in str(error.value).lower()


def test_scripted_provider_exhaustion_is_explicit():
    scripted = ScriptedProvider([])
    with pytest.raises(ProviderUnavailable):
        GuardedProvider(scripted).generate(
            _default_request(), TypeAdapter(IRGenerationOutcome)
        )


# --- offline network guard -------------------------------------------------


def _inet_is_already_blocked() -> bool:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return True
    probe.close()
    return False


def test_offline_guard_blocks_inet_but_preserves_unix():
    session_guard_active = _inet_is_already_blocked()
    restore = install_offline_network_guard()
    try:
        for family in (socket.AF_INET, socket.AF_INET6):
            with pytest.raises(OSError):
                socket.socket(family, socket.SOCK_STREAM)
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix_socket.close()
    finally:
        restore()
    # Restoring returns to whatever was installed before, which is the session
    # guard when the suite runs with CEREBRO_TEST_NO_NETWORK=1.
    assert _inet_is_already_blocked() is session_guard_active


def test_guarded_gateway_opens_no_socket_when_offline_guard_is_active():
    restore = install_offline_network_guard()
    try:
        gateway = _gateway(_ok_response)
        generation = gateway.generate(
            "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
        )
        assert isinstance(generation.usage.cost_usd, Decimal)
    finally:
        restore()


# --- organizer environment naming -----------------------------------------


def test_organizer_llm_variable_names_are_accepted_as_aliases():
    gateway = _gateway(
        _ok_response,
        environment={
            "CEREBRO_LLM_BASE_URL": "https://organizer.invalid/v1",
            "CEREBRO_LLM_API_KEY": SECRET,
            "CEREBRO_LLM_MODEL": "organizer-llm-model",
        },
    )
    assert gateway.model == "organizer-llm-model"
    assert gateway.provider == "organizer.invalid"


def test_canonical_names_win_over_the_llm_aliases():
    gateway = _gateway(
        _ok_response,
        environment={
            **BASE_ENVIRONMENT,
            "CEREBRO_LLM_MODEL": "alias-model",
            "CEREBRO_LLM_BASE_URL": "https://alias.invalid/v1",
        },
    )
    assert gateway.model == "organizer-model"
    assert gateway.provider == "organizer.invalid"


def test_absent_revision_falls_back_to_the_model_identifier():
    environment = {
        key: value
        for key, value in BASE_ENVIRONMENT.items()
        if key != "CEREBRO_MODEL_REVISION"
    }
    gateway = _gateway(_ok_response, environment=environment)
    assert gateway.model_revision == "organizer-model"


def test_missing_configuration_still_fails_when_neither_spelling_is_present():
    with pytest.raises(ProviderConfigurationError) as error:
        _gateway(
            _ok_response,
            environment={"CEREBRO_LLM_BASE_URL": "https://organizer.invalid/v1"},
        )
    message = str(error.value)
    assert "CEREBRO_API_KEY" in message
    assert "CEREBRO_MODEL" in message


# --- schema mechanism negotiation ------------------------------------------


def _capture(handler_response):
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return handler_response(request)

    return payloads, handler


def test_json_schema_requests_carry_the_actual_output_schema():
    payloads, handler = _capture(_ok_response)
    _gateway(handler).generate(
        "text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome)
    )
    response_format = payloads[0]["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert isinstance(schema, dict)
    assert schema  # a name-only request lets a provider answer in prose
    assert response_format["json_schema"]["name"] == "text2sql_outcome"


def test_json_object_mechanism_sends_the_schema_as_an_instruction():
    payloads, handler = _capture(_ok_response)
    gateway = _gateway(
        handler,
        environment={**BASE_ENVIRONMENT, "CEREBRO_SCHEMA_MECHANISM": "json_object"},
    )
    gateway.generate("text2sql_outcome", "{}", TypeAdapter(IRGenerationOutcome))
    assert payloads[0]["response_format"] == {"type": "json_object"}
    system = payloads[0]["messages"][0]
    assert system["role"] == "system"
    assert "json" in system["content"].lower()
    assert payloads[0]["messages"][-1]["content"] == "{}"


def test_response_mode_alias_configures_the_mechanism():
    gateway = _gateway(
        _ok_response,
        environment={**BASE_ENVIRONMENT, "CEREBRO_LLM_RESPONSE_MODE": "json_object"},
    )
    assert gateway.schema_mechanism == "json_object"


def test_probe_negotiates_auto_mode_and_records_the_working_mechanism(tmp_path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        mechanism = (payload.get("response_format") or {}).get("type", "none")
        seen.append(mechanism)
        if mechanism == "json_schema":
            # Mirror the observed organizer behavior: prose, not JSON.
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "I see you've provided a"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"ok": True})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    gateway = _gateway(
        handler, environment={**BASE_ENVIRONMENT, "CEREBRO_SCHEMA_MECHANISM": "auto"}
    )
    receipt, path = probe_provider_schema(gateway=gateway, receipt_dir=tmp_path)
    assert seen[0] == "json_schema"
    assert receipt.schema_mechanism == "json_object"
    assert path is not None
    assert ProviderCapabilityReceipt.model_validate_json(path.read_bytes()) == receipt


def test_probe_reports_rejection_when_no_mechanism_produces_valid_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "still prose"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    gateway = _gateway(
        handler, environment={**BASE_ENVIRONMENT, "CEREBRO_SCHEMA_MECHANISM": "auto"}
    )
    with pytest.raises(ProviderRejected):
        probe_provider_schema(gateway=gateway, receipt_dir=None)


# --- deployment timeout and tolerant content extraction --------------------


def test_provider_timeout_is_deployment_configurable():
    from cerebro.hosted_provider import PROVIDER_TIMEOUT_MS_PER_ATTEMPT

    default_gateway = _gateway(_ok_response)
    assert default_gateway.timeout_ms == PROVIDER_TIMEOUT_MS_PER_ATTEMPT

    slow = _gateway(
        _ok_response,
        environment={**BASE_ENVIRONMENT, "CEREBRO_PROVIDER_TIMEOUT_MS": "90000"},
    )
    assert slow.timeout_ms == 90_000

    alias = _gateway(
        _ok_response,
        environment={**BASE_ENVIRONMENT, "CEREBRO_LLM_TIMEOUT_MS": "45000"},
    )
    assert alias.timeout_ms == 45_000


@pytest.mark.parametrize("invalid", ["0", "-1", "not-a-number"])
def test_invalid_configured_timeout_is_a_configuration_error(invalid):
    with pytest.raises(ProviderConfigurationError):
        _gateway(
            _ok_response,
            environment={**BASE_ENVIRONMENT, "CEREBRO_PROVIDER_TIMEOUT_MS": invalid},
        )


@pytest.mark.parametrize(
    "content",
    [
        '{"ok": true}',
        '```json\n{"ok": true}\n```',
        '```\n{"ok": true}\n```',
        'Here you go:\n{"ok": true}',
    ],
)
def test_json_content_is_extracted_from_fences_and_surrounding_prose(content):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    from cerebro.prompting import ProviderProbe

    generation = _gateway(handler).generate(
        "provider_probe", "{}", TypeAdapter(ProviderProbe)
    )
    assert generation.output.ok is True


def test_reasoning_content_is_never_used_as_the_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "reasoning_content": 'let me think... {"ok": false}',
                            "content": '{"ok": true}',
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    from cerebro.prompting import ProviderProbe

    generation = _gateway(handler).generate(
        "provider_probe", "{}", TypeAdapter(ProviderProbe)
    )
    assert generation.output.ok is True


def test_prose_only_content_still_fails_as_a_semantic_decode_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "I cannot comply."}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    from pydantic import ValidationError

    from cerebro.prompting import ProviderProbe

    with pytest.raises(ValidationError):
        _gateway(handler).generate("provider_probe", "{}", TypeAdapter(ProviderProbe))
