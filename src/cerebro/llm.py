from __future__ import annotations

import json
import logging
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .settings import Settings

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)

# Reasons a request can fail for that are worth retrying with backoff. Anything else
# (bad request, auth, unsupported response_format, invalid JSON) will not improve on retry.
_RETRYABLE_REASONS = {"timeout", "rate_limited", "server_error", "connection_error"}


def classify_llm_error(exc: Exception) -> str:
    """Classify a chat-completions failure so callers can decide whether to retry and
    give the user an actionable reason instead of a generic "generation failed"."""
    try:
        import openai
    except ImportError:
        openai = None  # type: ignore[assignment]
    if openai is not None:
        if isinstance(exc, openai.APITimeoutError):
            return "timeout"
        if isinstance(exc, openai.RateLimitError):
            return "rate_limited"
        if isinstance(exc, openai.InternalServerError):
            return "server_error"
        if isinstance(exc, openai.APIConnectionError):
            return "connection_error"
        if isinstance(exc, openai.APIStatusError):
            return "status_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "unknown"


class GenerationOutputError(RuntimeError):
    def __init__(self, schema_name: str):
        self.schema_name = schema_name
        super().__init__(f"Model output did not match the required {schema_name} schema")

    @property
    def public_message(self) -> str:
        return f"Model output did not match the required {self.schema_name} schema."


def _decode_json_content(content: str) -> object:
    stripped = content.strip()
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
        stripped = "\n".join(lines[1:-1]).strip()
    return json.loads(stripped)


def _strict_json_schema(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


class OpenAICompatibleGateway:
    """One typed boundary for OpenAI-compatible chat-completions endpoints."""

    def __init__(self, settings: Settings, client: object | None = None):
        if not settings.llm_configured:
            raise RuntimeError("Configure CEREBRO_LLM_API_KEY and CEREBRO_LLM_MODEL")
        self.settings = settings
        self.name = settings.llm_provider_id
        self.display_name = settings.llm_provider_name
        self.model = str(settings.llm_model)
        self.resolved_response_mode: str | None = None
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Install the 'ai' extra to enable live model calls") from exc
            client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=float(settings.llm_timeout_seconds),
                max_retries=1,
            )
        self.client = client

    def _call_with_retry(self, **kwargs: object) -> object:
        """Call chat.completions.create, retrying transient failures (timeout, rate limit,
        connection error, 5xx) with exponential backoff. Non-transient failures (bad request,
        auth, unsupported response_format) are raised immediately since retrying won't help."""
        attempts = max(1, self.settings.llm_max_retries + 1)
        delay = self.settings.llm_retry_backoff_seconds
        timeout = float(self.settings.llm_timeout_seconds)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.client.chat.completions.create(timeout=timeout, **kwargs)  # type: ignore[attr-defined]
            except Exception as exc:
                reason = classify_llm_error(exc)
                exc.cerebro_reason = reason  # type: ignore[attr-defined]
                exc.cerebro_attempts = attempt  # type: ignore[attr-defined]
                last_exc = exc
                if reason not in _RETRYABLE_REASONS or attempt == attempts:
                    raise
                logger.warning(
                    "LLM request failed (%s) on attempt %d/%d; retrying in %.1fs: %s",
                    reason, attempt, attempts, delay, exc,
                )
                time.sleep(delay)
                delay *= 2
                if reason == "timeout":
                    timeout = min(timeout * self.settings.llm_timeout_backoff_multiplier, 600.0)
        assert last_exc is not None  # pragma: no cover - loop always returns or raises
        raise last_exc

    def _extra_body(self, thinking: bool) -> dict[str, object]:
        """GLM-5.2's non-standard reasoning toggle (spec 031). Omitted entirely
        unless the caller explicitly asks for reduced reasoning AND the
        configured provider is confirmed to accept the field — every other
        call stays byte-identical to before this parameter existed."""
        if thinking or not self.settings.llm_supports_reasoning_control:
            return {}
        return {"extra_body": {"thinking": {"type": "disabled"}}}

    def generate(
        self,
        schema_name: str,
        prompt: str,
        output_model: type[OutputT],
        *,
        thinking: bool = True,
        max_output_tokens: int | None = None,
    ) -> OutputT:
        modes = [self.settings.llm_response_mode]
        if modes[0] == "auto":
            modes = ["json_schema", "json_object"]
        last_error: Exception | None = None
        for index, mode in enumerate(modes):
            content: str | None = None
            try:
                response_format: dict[str, object]
                if mode == "json_schema":
                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": _strict_json_schema(output_model),
                        },
                    }
                else:
                    response_format = {"type": "json_object"}
                user_prompt = prompt
                if mode == "json_object":
                    user_prompt += "\n\nReturn JSON matching this schema:\n" + json.dumps(output_model.model_json_schema())
                response = self._call_with_retry(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Return only concise, syntactically valid JSON matching the requested schema. "
                            "Escape quotes inside strings. Never request credentials or hidden data. Be direct and "
                            "efficient: do not restate the input or add unrequested commentary, since large inputs "
                            "must still complete within the request timeout.",
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=response_format,
                    max_tokens=max_output_tokens if max_output_tokens is not None else self.settings.llm_max_output_tokens,
                    **self._extra_body(thinking),
                )
                content = response.choices[0].message.content
                if not content:
                    raise GenerationOutputError(schema_name)
                self.resolved_response_mode = mode
                return output_model.model_validate(_decode_json_content(content))
            except GenerationOutputError:
                raise
            except Exception as exc:
                last_error = exc
                if index + 1 == len(modes) and content and isinstance(exc, (json.JSONDecodeError, ValidationError)):
                    return self._repair_json(
                        schema_name, content, output_model, exc,
                        thinking=thinking, max_output_tokens=max_output_tokens,
                    )
                if index + 1 == len(modes) or not self._response_format_error(exc):
                    if content and isinstance(exc, (json.JSONDecodeError, ValidationError)):
                        raise GenerationOutputError(schema_name) from exc
                    raise RuntimeError(f"Provider failed to return valid {schema_name}: {exc}") from exc
        raise RuntimeError(f"Provider failed to return valid {schema_name}: {last_error}")

    def _repair_json(
        self,
        schema_name: str,
        invalid_content: str,
        output_model: type[OutputT],
        validation_error: Exception,
        *,
        thinking: bool = True,
        max_output_tokens: int | None = None,
    ) -> OutputT:
        try:
            response = self._call_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Repair the supplied model output. Return only syntactically valid JSON matching the schema; do not add facts.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "schema_name": schema_name,
                                "schema": output_model.model_json_schema(),
                                "validation_error": str(validation_error),
                                "invalid_output": invalid_content,
                            }
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                max_tokens=max_output_tokens if max_output_tokens is not None else self.settings.llm_max_output_tokens,
                **self._extra_body(thinking),
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("Provider returned no repaired content")
            self.resolved_response_mode = "json_object_repair"
            return output_model.model_validate(_decode_json_content(content))
        except Exception as exc:
            raise GenerationOutputError(schema_name) from exc

    @staticmethod
    def _response_format_error(exc: Exception) -> bool:
        if isinstance(exc, (json.JSONDecodeError, ValidationError)):
            return True
        status = getattr(exc, "status_code", None)
        message = str(exc).lower()
        return status in {400, 404, 415, 422} or any(
            token in message for token in ("response_format", "json_schema", "structured output", "unsupported")
        )


def gateway_from_environment(settings: Settings | None = None) -> OpenAICompatibleGateway | None:
    active = settings or Settings.from_environment()
    return OpenAICompatibleGateway(active) if active.llm_configured else None
