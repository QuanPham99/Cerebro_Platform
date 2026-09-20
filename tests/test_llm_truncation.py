"""Spec 032 — a reply cut off at the output-token budget is recovered with one
wider retry instead of being reported as a schema failure."""

from types import SimpleNamespace

import pytest

from cerebro.llm import GenerationOutputError, OpenAICompatibleGateway
from cerebro.models import AnswerPayload, SQLProposal
from cerebro.settings import Settings

TRUNCATED = '{"answer":"Tổng giá trị giao dịch gian lận theo chi nhánh: Branch 000: 1.043.210; Bra'
COMPLETE = '{"answer":"Ba chi nhánh dẫn đầu chiếm phần lớn giá trị gian lận."}'


def _settings(ceiling: int = 8192) -> Settings:
    return Settings(
        database_path=None,
        database_schema="main",
        llm_base_url="http://example.test/v1",
        llm_api_key="secret-value",
        llm_model="test-model",
        llm_response_mode="json_schema",
        embedding_model=None,
        llm_provider_id="greennode-glm",
        llm_provider_name="GreenNode",
        llm_max_output_tokens=ceiling,
    )


class BudgetedCompletions:
    """Replies in order; each entry is (content, finish_reason). A missing
    finish_reason models a provider that does not report one at all."""

    def __init__(self, replies):
        self.calls: list[dict] = []
        self.replies = replies

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, finish_reason = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        if finish_reason is not ...:
            choice.finish_reason = finish_reason
        return SimpleNamespace(choices=[choice])


def _gateway(replies, ceiling: int = 8192) -> tuple[OpenAICompatibleGateway, BudgetedCompletions]:
    completions = BudgetedCompletions(replies)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAICompatibleGateway(_settings(ceiling), client=client), completions


def test_truncated_reply_is_retried_once_with_a_larger_budget():
    """T-3201 / AC-3201."""
    gateway, completions = _gateway([(TRUNCATED, "length"), (COMPLETE, "stop")])

    answer = gateway.generate("database_answer", "narrate", AnswerPayload, max_output_tokens=512)

    assert answer.answer.startswith("Ba chi nhánh")
    assert len(completions.calls) == 2
    assert completions.calls[0]["max_tokens"] == 512
    assert completions.calls[1]["max_tokens"] > 512
    assert completions.calls[1]["max_tokens"] <= 8192
    # The wider retry stays in the mode that was already negotiated.
    assert {call["response_format"]["type"] for call in completions.calls} == {"json_schema"}


def test_no_retry_when_the_budget_is_already_at_the_configured_ceiling():
    """T-3202 / AC-3202, AC-3205: a wider ask is impossible, so fail once."""
    gateway, completions = _gateway([(TRUNCATED, "length")], ceiling=512)

    with pytest.raises(GenerationOutputError):
        gateway.generate("database_answer", "narrate", AnswerPayload, max_output_tokens=512)

    assert len(completions.calls) == 1


def test_second_truncated_reply_fails_without_further_retries():
    """T-3203 / AC-3203."""
    gateway, completions = _gateway([(TRUNCATED, "length"), (TRUNCATED, "length")])

    with pytest.raises(GenerationOutputError) as excinfo:
        gateway.generate("database_answer", "narrate", AnswerPayload, max_output_tokens=512)

    assert excinfo.value.schema_name == "database_answer"
    assert len(completions.calls) == 2


@pytest.mark.parametrize("finish_reason", ["stop", None, ...])
def test_untruncated_reply_resolves_in_one_call(finish_reason):
    """T-3204 / AC-3204: only finish_reason 'length' escalates the budget."""
    gateway, completions = _gateway([(COMPLETE, finish_reason)])

    assert gateway.generate("database_answer", "narrate", AnswerPayload, max_output_tokens=512)
    assert len(completions.calls) == 1
    assert completions.calls[0]["max_tokens"] == 512


def test_recovery_applies_to_every_schema_not_just_database_answer():
    """T-3205 / FR-3205."""
    gateway, completions = _gateway([
        (TRUNCATED, "length"),
        ('{"sql":"SELECT 1","explanation":"safe"}', "stop"),
    ])

    proposal = gateway.generate("sql_proposal", "propose", SQLProposal, max_output_tokens=256)

    assert proposal.sql == "SELECT 1"
    assert len(completions.calls) == 2
    assert completions.calls[1]["max_tokens"] > completions.calls[0]["max_tokens"]


def test_retry_budget_never_exceeds_the_configured_ceiling():
    """AC-3205."""
    gateway, completions = _gateway([(TRUNCATED, "length"), (COMPLETE, "stop")], ceiling=1024)

    gateway.generate("database_answer", "narrate", AnswerPayload, max_output_tokens=512)

    assert completions.calls[1]["max_tokens"] == 1024


def test_empty_reply_still_fails_immediately():
    """EC-3205: an empty body is not truncation and buys no retry."""
    gateway, completions = _gateway([("", "stop")])

    with pytest.raises(GenerationOutputError):
        gateway.generate("database_answer", "narrate", AnswerPayload, max_output_tokens=512)

    assert len(completions.calls) == 1
