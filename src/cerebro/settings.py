from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from .paths import DEFAULT_BUNDLE, ROOT

ACTIVE_BUNDLE_POINTER = ROOT / "artifacts" / "active-bundle.json"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class Settings:
    database_path: Path | None
    database_schema: str
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str | None
    llm_response_mode: str
    embedding_model: str | None
    query_row_limit: int = 100
    query_timeout_seconds: int = 10
    llm_provider_id: str = "openai-compatible"
    llm_provider_name: str = "OpenAI-compatible"
    llm_timeout_seconds: int = 120
    llm_max_output_tokens: int = 8192
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 2.0
    llm_timeout_backoff_multiplier: float = 1.5
    basic_auth_user: str | None = None
    basic_auth_password: str | None = None

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def basic_auth_enabled(self) -> bool:
        return bool(self.basic_auth_user and self.basic_auth_password)

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        database = _clean(os.getenv("CEREBRO_DATABASE_PATH"))
        response_mode = _clean(os.getenv("CEREBRO_LLM_RESPONSE_MODE")) or "auto"
        if response_mode not in {"auto", "json_schema", "json_object"}:
            raise ValueError("CEREBRO_LLM_RESPONSE_MODE must be auto, json_schema, or json_object")
        return cls(
            database_path=Path(database).expanduser() if database else None,
            database_schema=_clean(os.getenv("CEREBRO_DATABASE_SCHEMA")) or "main",
            llm_base_url=_clean(os.getenv("CEREBRO_LLM_BASE_URL")) or "https://api.openai.com/v1",
            llm_api_key=_clean(os.getenv("CEREBRO_LLM_API_KEY")) or _clean(os.getenv("OPENAI_API_KEY")),
            llm_model=_clean(os.getenv("CEREBRO_LLM_MODEL")) or _clean(os.getenv("CEREBRO_OPENAI_MODEL")),
            llm_response_mode=response_mode,
            embedding_model=_clean(os.getenv("CEREBRO_EMBEDDING_MODEL")),
            query_row_limit=max(1, min(int(os.getenv("CEREBRO_QUERY_ROW_LIMIT", "100")), 1000)),
            query_timeout_seconds=max(1, min(int(os.getenv("CEREBRO_QUERY_TIMEOUT_SECONDS", "10")), 60)),
            llm_provider_id=_clean(os.getenv("CEREBRO_LLM_PROVIDER_ID")) or "openai-compatible",
            llm_provider_name=_clean(os.getenv("CEREBRO_LLM_PROVIDER_NAME")) or "OpenAI-compatible",
            llm_timeout_seconds=max(10, min(int(os.getenv("CEREBRO_LLM_TIMEOUT_SECONDS", "120")), 600)),
            llm_max_output_tokens=max(256, min(int(os.getenv("CEREBRO_LLM_MAX_OUTPUT_TOKENS", "8192")), 32768)),
            llm_max_retries=max(0, min(int(os.getenv("CEREBRO_LLM_MAX_RETRIES", "2")), 5)),
            llm_retry_backoff_seconds=max(0.5, min(float(os.getenv("CEREBRO_LLM_RETRY_BACKOFF_SECONDS", "2")), 30)),
            llm_timeout_backoff_multiplier=max(
                1.0, min(float(os.getenv("CEREBRO_LLM_TIMEOUT_BACKOFF_MULTIPLIER", "1.5")), 3.0)
            ),
            basic_auth_user=_clean(os.getenv("CEREBRO_BASIC_AUTH_USER")),
            basic_auth_password=_clean(os.getenv("CEREBRO_BASIC_AUTH_PASSWORD")),
        )

    def public_status(self) -> dict[str, object]:
        database_exists = bool(self.database_path and self.database_path.is_file())
        parsed_url = urlsplit(self.llm_base_url)
        safe_netloc = parsed_url.hostname or ""
        if parsed_url.port:
            safe_netloc += f":{parsed_url.port}"
        safe_base_url = urlunsplit((parsed_url.scheme, safe_netloc, parsed_url.path, "", ""))
        return {
            "llm_configured": self.llm_configured,
            "provider_id": self.llm_provider_id,
            "provider_name": self.llm_provider_name,
            "model": self.llm_model,
            "base_url": safe_base_url,
            "response_mode": self.llm_response_mode,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_max_output_tokens": self.llm_max_output_tokens,
            "llm_max_retries": self.llm_max_retries,
            "llm_retry_backoff_seconds": self.llm_retry_backoff_seconds,
            "llm_timeout_backoff_multiplier": self.llm_timeout_backoff_multiplier,
            "api_key_configured": bool(self.llm_api_key),
            "embedding_model": self.embedding_model,
            "database_configured": self.database_path is not None,
            "database_reachable": database_exists,
            "database_schema": self.database_schema,
            "query_row_limit": self.query_row_limit,
            "query_timeout_seconds": self.query_timeout_seconds,
        }


def resolve_active_bundle(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    configured = _clean(os.getenv("CEREBRO_BUNDLE_PATH"))
    if configured:
        return Path(configured).expanduser()
    if ACTIVE_BUNDLE_POINTER.is_file():
        try:
            payload = json.loads(ACTIVE_BUNDLE_POINTER.read_text(encoding="utf-8"))
            candidate = Path(str(payload["path"]))
            if candidate.is_dir():
                return candidate
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            pass
    return DEFAULT_BUNDLE
