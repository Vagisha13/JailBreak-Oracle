"""E-20 observability tests: structured log context whitelist, secret masking,
and the structured LLM-call / campaign-scoped provider-error events.
"""
import json
import logging
import uuid

import pytest

from app.core.logging import JsonFormatter, MaskingFormatter, get_logger


class _CaptureHandler(logging.Handler):
    """Collects formatted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture_logger(name: str):
    logger = get_logger(name)
    handler = _CaptureHandler()
    logger.addHandler(handler)
    return logger, handler


@pytest.fixture
def masked():
    return MaskingFormatter()


def _format(formatter, message: str) -> str:
    record = logging.LogRecord(
        "test.observability", logging.INFO, __file__, 1, message, (), None
    )
    return formatter.format(record)


# --- Secret masking -----------------------------------------------------------


def test_masking_redacts_key_value_forms(masked):
    out = _format(
        masked,
        "cfg api_key=sk-abcdef123456 x-api-key=abc123 "
        "client_secret=xyz789 password=hunter2 access_token=aaa111",
    )
    assert "sk-abcdef123456" not in out
    assert "abc123" not in out
    assert "xyz789" not in out
    assert "hunter2" not in out
    assert "aaa111" not in out
    for marker in ("api_key=***", "x-api-key=***", "client_secret=***",
                   "password=***", "access_token=***"):
        assert marker in out


def test_masking_redacts_bearer_tokens_and_jwt(masked):
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.sig"
    out = _format(masked, f"Authorization: Bearer {jwt} and raw token {jwt}")
    assert jwt not in out
    # Both the header-bound JWT and the raw JWT are redacted.
    assert out.count("eyJ***") >= 2


def test_masking_redacts_raw_sk_values(masked):
    out = _format(masked, "received sk-aaaabbbbcccc1234 from provider")
    assert "sk-aaaabbbbcccc1234" not in out
    assert "sk-***" in out


def test_masking_single_pass_no_double_redaction(masked):
    # A redacted construct must not re-trigger a sibling rule (x-api-key also
    # contains api-key; client_secret also contains secret).
    out = _format(
        masked, "x-api-key=abc123 client_secret=xyz789 api_key=k1"
    )
    assert "=***=***" not in out
    assert out.count("***") == 3


def test_masking_leaves_plain_logs_untouched(masked):
    out = _format(masked, "campaign completed with 12 attacks and 3 findings")
    assert "***" not in out
    assert "campaign completed" in out


# --- Structured context whitelist ---------------------------------------------


def test_json_formatter_whitelists_telemetry_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "test.observability", logging.INFO, __file__, 1, "msg", (), None
    )
    record.__dict__.update(
        {
            "event_name": "provider.llm_call",
            "campaign_id": "camp-1",
            "role": "attacker",
            "prompt_tokens": 300,
            "completion_tokens": 120,
            "total_tokens": 420,
            "transient": False,
            "sneaky_secret": "should-not-leak",
        }
    )
    payload = json.loads(formatter.format(record))
    assert payload["event_name"] == "provider.llm_call"
    assert payload["campaign_id"] == "camp-1"
    assert payload["role"] == "attacker"
    assert payload["prompt_tokens"] == 300
    assert payload["total_tokens"] == 420
    assert payload["transient"] is False
    assert "sneaky_secret" not in payload


# --- Structured llm_call / llm_error events -----------------------------------


def _fake_success_response(text="Safe completion."):
    class _Message:
        content = text

    class _Choice:
        message = _Message()

    class _Usage:
        prompt_tokens = 3
        completion_tokens = 5
        total_tokens = 8

    response = object.__new__(type("FakeCompletion", (), {}))
    response.choices = [_Choice()]
    response.usage = _Usage()
    return response


@pytest.mark.asyncio
async def test_litellm_success_emits_llm_call_event(monkeypatch):
    import litellm

    from app.targets.litellm import LiteLLMTargetProvider

    async def fake_acompletion(**kwargs):
        return _fake_success_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    logger, handler = _capture_logger("provider.litellm")
    try:
        provider = LiteLLMTargetProvider(default_model="gpt-test", max_retries=0)
        result = await provider.execute("ping", {"model": "gpt-test"})
        assert result.error is None

        events = [
            r for r in handler.records
            if getattr(r, "event_name", None) == "provider.llm_call"
        ]
        assert len(events) == 1
        rec = events[0]
        assert rec.model == "gpt-test"
        assert rec.provider == "litellm"
        assert rec.prompt_tokens == 3
        assert rec.completion_tokens == 5
        assert rec.total_tokens == 8
        assert rec.latency_ms > 0
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_budget_wrapper_error_event_carries_campaign_context():
    from app.schemas.target import TargetResponse
    from app.services.budget import BudgetedTargetProvider
    from app.targets.base import TargetProvider

    class ErrorProvider(TargetProvider):
        async def execute(self, prompt, config):
            return TargetResponse(
                response_text="", latency_ms=1.0, error="Simulated provider failure"
            )

    campaign_id = uuid.UUID("00000000-0000-0000-0000-000000000042")
    wrapped = BudgetedTargetProvider(
        delegate=ErrorProvider(),
        role="attacker",
        default_model="gpt-test",
        experiment_id=campaign_id,
    )

    logger, handler = _capture_logger("budget")
    try:
        await wrapped.execute("prompt", {})
        events = [
            r for r in handler.records
            if getattr(r, "event_name", None) == "provider.llm_error"
        ]
        assert len(events) == 1
        rec = events[0]
        assert rec.campaign_id == str(campaign_id)
        assert rec.role == "attacker"
        assert rec.model == "gpt-test"
        assert "Simulated provider failure" in rec.error_message
        assert rec.provider == "ErrorProvider"
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_budget_wrapper_success_does_not_double_log():
    from app.schemas.target import TargetResponse
    from app.services.budget import BudgetedTargetProvider
    from app.targets.base import TargetProvider

    class OkProvider(TargetProvider):
        async def execute(self, prompt, config):
            return TargetResponse(response_text="fine", latency_ms=1.0)

    wrapped = BudgetedTargetProvider(
        delegate=OkProvider(), role="target", experiment_id=uuid.uuid4()
    )

    logger, handler = _capture_logger("budget")
    try:
        await wrapped.execute("prompt", {})
        error_events = [
            r for r in handler.records
            if getattr(r, "event_name", None) == "provider.llm_error"
        ]
        assert error_events == []
    finally:
        logger.removeHandler(handler)
