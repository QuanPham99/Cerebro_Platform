from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .settings import Settings

OutputT = TypeVar("OutputT", bound=BaseModel)


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

    def generate(self, schema_name: str, prompt: str, output_model: type[OutputT]) -> OutputT:
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
                response = self.client.chat.completions.create(  # type: ignore[attr-defined]
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Return only concise, syntactically valid JSON matching the requested schema. Escape quotes inside strings. Never request credentials or hidden data.",
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=response_format,
                    max_tokens=self.settings.llm_max_output_tokens,
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
                    return self._repair_json(schema_name, content, output_model, exc)
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
    ) -> OutputT:
        try:
            response = self.client.chat.completions.create(  # type: ignore[attr-defined]
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
                max_tokens=self.settings.llm_max_output_tokens,
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
