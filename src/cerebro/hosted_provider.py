"""Organizer transport plus the guarded, scripted, and cassette adapters.

`OrganizerModelGateway` is the only place that speaks HTTP, and it stays
OpenAI-compatible without importing a vendor SDK. `GuardedProvider` is the only
boundary orchestration consumes: it accepts a locally built
`GuardedGenerationRequest` and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from .models import (
    GuardedGenerationRequest,
    ProviderCapabilityReceipt,
)
from .prompting import (
    PromptAuthorizationError,
    ProviderProbe,
    _authorize_prompt_envelope,
    _build_prompt_envelope,
    _render_prompt,
    empty_probe_snapshot,
    prompt_snapshot_view,
)
from .text2sql_provider import (
    EgressBlocked,
    ProviderConfigurationError,
    ProviderGeneration,
    ProviderRejected,
    ProviderUnavailable,
    ProviderUsage,
    TransportAttempt,
)

OutputT = TypeVar("OutputT")

MAX_TRANSPORT_ATTEMPTS_PER_SEMANTIC_CALL = 2
PROVIDER_TIMEOUT_MS_PER_ATTEMPT = 20_000
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# Most strict first: a provider that honors a real schema is preferred over one
# that only guarantees JSON, which is preferred over prompt-only instruction.
_AUTO_SCHEMA_MECHANISMS = ("json_schema", "json_object", "none")
_BLOCKED_SOCKET_FAMILIES = (socket.AF_INET, socket.AF_INET6)


def install_offline_network_guard() -> Callable[[], None]:
    """Block IPv4/IPv6 socket creation while leaving `AF_UNIX` usable."""
    original_socket = socket.socket

    class _GuardedSocket(original_socket):  # type: ignore[misc,valid-type]
        def __init__(self, family=socket.AF_INET, *args: Any, **kwargs: Any) -> None:
            if family in _BLOCKED_SOCKET_FAMILIES:
                raise OSError("outbound IPv4/IPv6 networking is disabled for tests")
            super().__init__(family, *args, **kwargs)

    socket.socket = _GuardedSocket  # type: ignore[assignment]

    def restore() -> None:
        socket.socket = original_socket  # type: ignore[assignment]

    return restore


def _json_payload_text(content: str) -> str:
    """Return the JSON object text from a model reply, tolerating wrappers.

    Some providers wrap the object in a markdown fence or prefix a sentence.
    Extraction is bounded to the outermost balanced braces and never repairs or
    invents content: prose with no JSON object stays invalid and fails decoding.
    """
    text = content.strip()
    if text.startswith("```"):
        fenced = text.split("```")
        if len(fenced) >= 3:
            body = fenced[1]
            if body.lower().startswith("json"):
                body = body[4:]
            text = body.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _configured(environ: Mapping[str, str], *names: str) -> str:
    """Return the first configured value among equivalent variable names.

    The canonical `CEREBRO_*` names win; the `CEREBRO_LLM_*` spellings are
    accepted because that is how the organizer environment is published.
    """
    for name in names:
        value = environ.get(name, "").strip()
        if value:
            return value
    return ""


def _sanitized_provider_name(base_url: str) -> str:
    host = urlsplit(base_url).hostname
    if not host:
        raise ProviderConfigurationError("provider base URL has no host")
    return host


class OrganizerModelGateway:
    """Transport only. It never builds a prompt and never sees a snapshot."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_revision: str,
        client: httpx.Client,
        backoff_seconds: float = 0.5,
        max_attempts: int = MAX_TRANSPORT_ATTEMPTS_PER_SEMANTIC_CALL,
        schema_mechanism: str = "json_schema",
        timeout_ms: int = PROVIDER_TIMEOUT_MS_PER_ATTEMPT,
    ) -> None:
        self.timeout_ms = timeout_ms
        self._base_url = base_url.rstrip("/")
        # The credential is held privately and only ever written to one header.
        self._api_key = api_key
        self.model = model
        self.model_revision = model_revision
        self.provider = _sanitized_provider_name(base_url)
        self.schema_mechanism = schema_mechanism
        self._client = client
        self._backoff_seconds = backoff_seconds
        self._max_attempts = max_attempts

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
        backoff_seconds: float = 0.5,
        max_attempts: int = MAX_TRANSPORT_ATTEMPTS_PER_SEMANTIC_CALL,
        timeout_ms: int = PROVIDER_TIMEOUT_MS_PER_ATTEMPT,
    ) -> OrganizerModelGateway:
        base_url = _configured(environ, "CEREBRO_BASE_URL", "CEREBRO_LLM_BASE_URL")
        api_key = _configured(environ, "CEREBRO_API_KEY", "CEREBRO_LLM_API_KEY")
        model = _configured(environ, "CEREBRO_MODEL", "CEREBRO_LLM_MODEL")
        revision = _configured(
            environ, "CEREBRO_MODEL_REVISION", "CEREBRO_LLM_MODEL_REVISION"
        )
        # An organizer that publishes no separate revision pins the model ID as
        # its own revision, so capability evidence always has a concrete value.
        revision = revision or model
        # Configuration errors name the missing variable, never its value.
        missing = [
            name
            for name, value in (
                ("CEREBRO_BASE_URL", base_url),
                ("CEREBRO_API_KEY", api_key),
                ("CEREBRO_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise ProviderConfigurationError(
                "missing provider configuration: " + ", ".join(sorted(missing))
            )
        mechanism = (
            _configured(
                environ, "CEREBRO_SCHEMA_MECHANISM", "CEREBRO_LLM_RESPONSE_MODE"
            )
            or "json_schema"
        )
        # FR-716 fixes the 20s default; a slower reasoning model is a deployment
        # configuration choice, not a hard-coded provider assumption.
        configured_timeout = _configured(
            environ, "CEREBRO_PROVIDER_TIMEOUT_MS", "CEREBRO_LLM_TIMEOUT_MS"
        )
        if configured_timeout:
            try:
                timeout_ms = int(configured_timeout)
            except ValueError as error:
                raise ProviderConfigurationError(
                    "provider timeout must be a positive whole number of milliseconds"
                ) from error
            if timeout_ms <= 0:
                raise ProviderConfigurationError(
                    "provider timeout must be a positive whole number of milliseconds"
                )
        client = httpx.Client(
            transport=transport, timeout=httpx.Timeout(timeout_ms / 1000)
        )
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            model_revision=revision,
            client=client,
            backoff_seconds=backoff_seconds,
            max_attempts=max_attempts,
            schema_mechanism=mechanism,
            timeout_ms=timeout_ms,
        )

    def __repr__(self) -> str:  # pragma: no cover - defensive redaction
        return (
            f"OrganizerModelGateway(provider={self.provider!r}, "
            f"model={self.model!r}, revision={self.model_revision!r})"
        )

    def _request_body(
        self,
        schema_name: str,
        prompt: str,
        output_adapter: TypeAdapter[OutputT],
        mechanism: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "temperature": 0}
        schema = output_adapter.json_schema()
        if mechanism == "json_schema":
            # A name-only request lets a provider answer in prose, so the actual
            # output schema always travels with the request.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        elif mechanism == "json_object":
            body["response_format"] = {"type": "json_object"}
        # Every mechanism carries the same fixed instruction. A reasoning model
        # otherwise answers in prose even when a response format is declared,
        # and this string is local contract text, never caller-supplied.
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Reply with exactly one JSON object and no prose, no "
                    "markdown, and no code fence. It must validate against "
                    f"this JSON Schema named {schema_name}: "
                    + json.dumps(schema, sort_keys=True)
                ),
            },
            {"role": "user", "content": prompt},
        ]
        body["messages"] = messages
        return body

    def generate(
        self,
        schema_name: str,
        prompt: str,
        output_adapter: TypeAdapter[OutputT],
        mechanism: str | None = None,
    ) -> ProviderGeneration[OutputT]:
        attempts: list[TransportAttempt] = []
        effective_mechanism = mechanism or self.schema_mechanism
        for ordinal in range(1, self._max_attempts + 1):
            started = time.monotonic()
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "authorization": f"Bearer {self._api_key}",
                        "content-type": "application/json",
                    },
                    json=self._request_body(
                        schema_name, prompt, output_adapter, effective_mechanism
                    ),
                )
            except httpx.TimeoutException:
                attempts.append(
                    self._attempt(ordinal, started, None, "provider_timeout")
                )
                self._sleep_before_retry(ordinal)
                continue
            except httpx.HTTPError:
                attempts.append(
                    self._attempt(ordinal, started, None, "provider_transport_error")
                )
                self._sleep_before_retry(ordinal)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                attempts.append(
                    self._attempt(
                        ordinal, started, response.status_code, "provider_retryable"
                    )
                )
                self._sleep_before_retry(ordinal)
                continue
            if response.status_code >= 400:
                attempts.append(
                    self._attempt(
                        ordinal, started, response.status_code, "provider_rejected"
                    )
                )
                # Sanitized: the provider body may echo the credential.
                raise ProviderRejected(
                    f"provider rejected the request with status {response.status_code}"
                )

            # A reply that arrives but does not decode is worth one more ask.
            # Re-sending the identical prompt is not semantic repair: no
            # violation is fed back, so the model is not being coached. Leaving
            # the remaining allowance unused meant one malformed reply ended the
            # whole request.
            try:
                decoded = self._decode(
                    response,
                    output_adapter,
                    (
                        *attempts,
                        self._attempt(
                            ordinal, started, response.status_code, None, "accepted"
                        ),
                    ),
                )
            except ValidationError:
                attempts.append(
                    self._attempt(
                        ordinal, started, response.status_code, "undecodable_reply"
                    )
                )
                if ordinal >= self._max_attempts:
                    raise
                self._sleep_before_retry(ordinal)
                continue
            return decoded

        raise ProviderUnavailable(
            f"provider unavailable after {len(attempts)} transport attempts"
        )

    def _sleep_before_retry(self, ordinal: int) -> None:
        if self._backoff_seconds > 0 and ordinal < self._max_attempts:
            time.sleep(self._backoff_seconds * (2 ** (ordinal - 1)))

    @staticmethod
    def _attempt(
        ordinal: int,
        started: float,
        status_code: int | None,
        error_code: str | None,
        outcome: str = "rejected",
    ) -> TransportAttempt:
        return TransportAttempt(
            ordinal=ordinal,
            outcome=outcome,  # type: ignore[arg-type]
            status_code=status_code,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            error_code=error_code,
        )

    def _decode(
        self,
        response: httpx.Response,
        output_adapter: TypeAdapter[OutputT],
        attempts: tuple[TransportAttempt, ...],
    ) -> ProviderGeneration[OutputT]:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ProviderRejected(
                "provider returned an unreadable envelope"
            ) from error
        declared = payload.get("schema_mechanism")
        if isinstance(declared, str) and declared:
            self.schema_mechanism = declared
        # A schema failure is a semantic decoding fault, not a transport fault:
        # it propagates as ValidationError for orchestration to classify.
        # Only `content` is ever decoded: a reasoning trace is never the output.
        output = output_adapter.validate_json(
            _json_payload_text(content)
            if isinstance(content, str)
            else json.dumps(content)
        )
        usage = payload.get("usage") or {}
        return ProviderGeneration[OutputT](
            output=output,
            transport_attempts=attempts,
            usage=ProviderUsage(
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
                cost_usd=Decimal(str(usage.get("cost_usd", "0") or "0")),
            ),
        )


class ScriptedProvider:
    """Deterministic offline transport double. It never reaches the network."""

    provider = "scripted"
    model = "scripted-model"
    model_revision = "scripted"
    schema_mechanism = "json_schema"

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, str]] = []

    def generate(
        self,
        schema_name: str,
        prompt: str,
        output_adapter: TypeAdapter[OutputT],
    ) -> ProviderGeneration[OutputT]:
        self.calls.append({"schema_name": schema_name, "prompt": prompt})
        if not self._outcomes:
            raise ProviderUnavailable("scripted provider has no remaining outcome")
        raw = self._outcomes.pop(0)
        payload = (
            raw if isinstance(raw, (dict, list, str)) else raw.model_dump(mode="json")
        )
        output = output_adapter.validate_python(payload)
        return ProviderGeneration[OutputT](
            output=output,
            transport_attempts=(
                TransportAttempt(ordinal=1, outcome="accepted", latency_ms=0),
            ),
            usage=ProviderUsage(),
        )


class CassetteProvider:
    """Replay recorded outcomes by mode. A missing entry fails loudly."""

    provider = "cassette"
    model = "cassette-model"
    model_revision = "cassette"
    schema_mechanism = "json_schema"

    def __init__(self, cassette_path: Path) -> None:
        self._path = Path(cassette_path)
        try:
            self._entries: dict[str, list[Any]] = json.loads(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderConfigurationError(
                "cassette is missing or unreadable"
            ) from error
        self.calls: list[dict[str, str]] = []

    def generate(
        self,
        schema_name: str,
        prompt: str,
        output_adapter: TypeAdapter[OutputT],
    ) -> ProviderGeneration[OutputT]:
        self.calls.append({"schema_name": schema_name, "prompt": prompt})
        mode = json.loads(prompt).get("mode", "")
        remaining = self._entries.get(mode) or []
        if not remaining:
            # Never fall through to live mode on a cassette miss.
            raise ProviderUnavailable(f"cassette has no entry for mode {mode!r}")
        payload = remaining.pop(0)
        output = output_adapter.validate_python(payload)
        return ProviderGeneration[OutputT](
            output=output,
            transport_attempts=(
                TransportAttempt(ordinal=1, outcome="accepted", latency_ms=0),
            ),
            usage=ProviderUsage(),
        )


class GuardedProvider:
    """The only provider boundary orchestration may consume."""

    def __init__(self, inner: Any, *, schema_name: str = "text2sql_outcome") -> None:
        self._inner = inner
        self._schema_name = schema_name

    @property
    def provider(self) -> str:
        return getattr(self._inner, "provider", "unknown")

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "unknown")

    @property
    def model_revision(self) -> str:
        return getattr(self._inner, "model_revision", "unknown")

    @property
    def schema_mechanism(self) -> str:
        return getattr(self._inner, "schema_mechanism", "json_schema")

    def generate(
        self,
        request: GuardedGenerationRequest,
        output_adapter: TypeAdapter[OutputT],
    ) -> ProviderGeneration[OutputT]:
        # Exact type identity, not structural similarity: a dictionary, string,
        # prebuilt envelope, or lookalike object is an egress attempt.
        if type(request) is not GuardedGenerationRequest:
            raise EgressBlocked("the provider boundary accepts only a guarded request")
        try:
            envelope = _build_prompt_envelope(request)
            _authorize_prompt_envelope(envelope, request)
        except PromptAuthorizationError as error:
            raise EgressBlocked(str(error)) from None
        return self._inner.generate(
            self._schema_name, _render_prompt(envelope), output_adapter
        )


def probe_provider_schema(
    *,
    gateway: OrganizerModelGateway,
    receipt_dir: Path | None,
    reference_snapshot: Any = None,
) -> tuple[ProviderCapabilityReceipt, Path | None]:
    """Perform one metadata-only schema call and record its capability receipt."""
    if reference_snapshot is None:
        from .prompting import GroundingPromptView

        probe_view = GroundingPromptView(
            semantic_version="probe",
            dialect="duckdb",
            supports_window=False,
            supports_set_operations=False,
        )
    else:
        probe_view = prompt_snapshot_view(empty_probe_snapshot(reference_snapshot))

    from .prompting import PromptEnvelope

    envelope = PromptEnvelope(
        mode="provider_probe",
        canonical_question="",
        snapshot=probe_view,
    )
    prompt = _render_prompt(envelope)
    adapter = TypeAdapter(ProviderProbe)

    # `auto` means the organizer has not declared one mechanism, so the probe
    # discovers it: one metadata-only call per candidate, most strict first, and
    # the receipt records the mechanism that actually produced valid output.
    candidates = (
        _AUTO_SCHEMA_MECHANISMS
        if gateway.schema_mechanism == "auto"
        else (gateway.schema_mechanism,)
    )
    working_mechanism: str | None = None
    for candidate in candidates:
        try:
            generation = gateway.generate(
                "provider_probe", prompt, adapter, mechanism=candidate
            )
        except (ValidationError, ProviderRejected):
            continue
        if generation.output.ok is True:
            working_mechanism = candidate
            break
    if working_mechanism is None:
        raise ProviderRejected("no schema mechanism produced a valid probe response")
    gateway.schema_mechanism = working_mechanism

    receipt = ProviderCapabilityReceipt(
        provider=gateway.provider,
        model=gateway.model,
        revision=gateway.model_revision or "unknown",
        schema_mechanism=working_mechanism,
    )
    if receipt_dir is None:
        return receipt, None

    payload = receipt.model_dump_json().encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    path = receipt_dir / f"{digest}.json"
    if not path.exists():
        path.write_bytes(payload)
    return receipt, path


_ = ValidationError  # transport decodes propagate this to orchestration
