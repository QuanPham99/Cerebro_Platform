from types import SimpleNamespace

from cerebro.llm import OpenAICompatibleGateway
from cerebro.models import AnswerPayload
from cerebro.settings import Settings


def _settings(mode="auto"):
    return Settings(
        database_path=None,
        database_schema="main",
        llm_base_url="http://example.test/v1",
        llm_api_key="secret-value",
        llm_model="test-model",
        llm_response_mode=mode,
        embedding_model=None,
        llm_provider_id="greennode-glm",
        llm_provider_name="GreenNode",
    )


class FakeCompletions:
    def __init__(self, reject_schema=False, content='{"answer":"ok"}'):
        self.calls = []
        self.reject_schema = reject_schema
        self.contents = content if isinstance(content, list) else [content]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_schema and kwargs["response_format"]["type"] == "json_schema":
            error = RuntimeError("json_schema unsupported")
            error.status_code = 400
            raise error
        content = self.contents[min(len(self.calls) - 1, len(self.contents) - 1)]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_gateway_uses_configured_model_and_typed_output():
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAICompatibleGateway(_settings("json_schema"), client=client)
    assert gateway.name == "greennode-glm"
    assert gateway.generate("answer", "hello", AnswerPayload).answer == "ok"
    assert completions.calls[0]["model"] == "test-model"
    assert completions.calls[0]["response_format"]["type"] == "json_schema"


def test_gateway_auto_falls_back_once_to_json_object():
    completions = FakeCompletions(reject_schema=True)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAICompatibleGateway(_settings(), client=client)
    assert gateway.generate("answer", "hello", AnswerPayload).answer == "ok"
    assert [call["response_format"]["type"] for call in completions.calls] == ["json_schema", "json_object"]
    assert gateway.resolved_response_mode == "json_object"


def test_gateway_accepts_json_wrapped_in_markdown_fence():
    completions = FakeCompletions(content='```json\n{"answer":"ok"}\n```')
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAICompatibleGateway(_settings("json_schema"), client=client)

    assert gateway.generate("answer", "hello", AnswerPayload).answer == "ok"


def test_gateway_auto_falls_back_when_schema_response_fails_validation():
    completions = FakeCompletions(content=['"wrong-shape"', '```json\n{"answer":"ok"}\n```'])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAICompatibleGateway(_settings(), client=client)

    assert gateway.generate("answer", "hello", AnswerPayload).answer == "ok"
    assert [call["response_format"]["type"] for call in completions.calls] == ["json_schema", "json_object"]
    assert gateway.resolved_response_mode == "json_object"


def test_gateway_repairs_malformed_json_once_after_compatibility_fallback():
    completions = FakeCompletions(content=['"wrong-shape"', '{"answer" "broken"}', '{"answer":"repaired"}'])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAICompatibleGateway(_settings(), client=client)

    assert gateway.generate("answer", "hello", AnswerPayload).answer == "repaired"
    assert [call["response_format"]["type"] for call in completions.calls] == ["json_schema", "json_object", "json_object"]
    assert gateway.resolved_response_mode == "json_object_repair"


def test_public_status_redacts_url_credentials_and_query():
    settings = _settings()
    settings = Settings(**{**settings.__dict__, "llm_base_url": "https://user:secret@example.test/v1?api_key=hidden"})
    assert settings.public_status()["base_url"] == "https://example.test/v1"
    assert settings.public_status()["provider_id"] == "greennode-glm"
    assert settings.public_status()["provider_name"] == "GreenNode"


def test_settings_loads_provider_identity_from_environment(monkeypatch):
    monkeypatch.setenv("CEREBRO_LLM_PROVIDER_ID", "custom-provider")
    monkeypatch.setenv("CEREBRO_LLM_PROVIDER_NAME", "Custom Provider")
    monkeypatch.setenv("CEREBRO_LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("CEREBRO_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CEREBRO_LLM_MODEL", "custom-model")
    monkeypatch.setenv("CEREBRO_LLM_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("CEREBRO_LLM_MAX_OUTPUT_TOKENS", "4096")

    settings = Settings.from_environment()

    assert settings.llm_provider_id == "custom-provider"
    assert settings.llm_provider_name == "Custom Provider"
    assert settings.llm_base_url == "https://provider.example/v1"
    assert settings.llm_api_key == "test-key"
    assert settings.llm_model == "custom-model"
    assert settings.llm_timeout_seconds == 180
    assert settings.llm_max_output_tokens == 4096
