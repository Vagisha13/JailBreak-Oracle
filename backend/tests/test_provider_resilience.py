"""LLM provider resilience tests: timeouts, retries with backoff, and error
normalization. No real provider calls are made - ``litellm.acompletion`` is
stubbed directly.
"""
import litellm
import pytest

from app.core.config import settings
from app.targets.litellm import LiteLLMTargetProvider, _normalize_error


def _success_response(text: str = "Safe completion.") -> object:
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


class FakeLitellm:
    """Stateful stand-in for ``litellm.acompletion``.

    ``exceptions`` is a list of exceptions to raise in sequence; once the list
    is exhausted the call succeeds (unless the last attempt broke the loop).
    """

    def __init__(self, exceptions=(), on_success=None):
        self.exceptions = list(exceptions)
        self.calls = 0
        self.on_success = on_success

    async def acompletion(self, **kwargs):
        self.calls += 1
        if self.calls <= len(self.exceptions):
            raise self.exceptions[self.calls - 1]
        if self.on_success is not None:
            self.on_success(kwargs)
        return _success_response()


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0.01)
    return LiteLLMTargetProvider(default_model="gpt-test", max_retries=2, timeout=5)


def _timeout():
    return litellm.Timeout(message="clock", model="gpt-test", llm_provider="openai")


def _server500():
    return litellm.InternalServerError(message="boom", llm_provider="openai", model="gpt-test")


def _auth_error():
    return litellm.AuthenticationError(message="bad key", llm_provider="openai", model="gpt-test")


def _bad_request():
    return litellm.BadRequestError(message="naughty param", model="gpt-test", llm_provider="openai")


@pytest.mark.asyncio
async def test_transient_timeout_retried_then_succeeds(monkeypatch, provider):
    fake = FakeLitellm([_timeout(), _timeout()])
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)

    result = await provider.execute("ping", {"model": "gpt-test"})

    assert result.error is None
    assert result.response_text == "Safe completion."
    assert fake.calls == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_transient_error_backoff_applies(monkeypatch, provider):
    slept = []

    async def _sleep(delay):
        slept.append(delay)

    monkeypatch.setattr("app.targets.litellm.asyncio.sleep", _sleep)
    fake = FakeLitellm([_server500(), _server500()])
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)

    result = await provider.execute("ping", {"model": "gpt-test"})

    assert result.response_text == "Safe completion."
    assert len(slept) == 2

    # delay = base * 2^attempt  (attempt starts at 0)
    assert slept[0] == pytest.approx(settings.LLM_RETRY_BACKOFF_SECONDS * 1)
    assert slept[1] == pytest.approx(settings.LLM_RETRY_BACKOFF_SECONDS * 2)


@pytest.mark.asyncio
async def test_transient_exhaustion_returns_normalized_error(monkeypatch, provider):
    fake = FakeLitellm([_timeout()] * 10)
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)

    result = await provider.execute("ping", {"model": "gpt-test"})

    assert result.error is not None
    assert "timed out" in result.error
    assert fake.calls == 3  # max_retries + 1


@pytest.mark.asyncio
async def test_permanent_auth_error_not_retried(monkeypatch, provider):
    fake = FakeLitellm([_auth_error()])
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)

    result = await provider.execute("ping", {"model": "gpt-test"})

    assert fake.calls == 1
    assert result.error is not None
    assert "authentication" in result.error.lower()


@pytest.mark.asyncio
async def test_permanent_bad_request_not_retried(monkeypatch, provider):
    fake = FakeLitellm([_bad_request()])
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)

    result = await provider.execute("ping", {"model": "gpt-test"})

    assert fake.calls == 1
    assert "rejected" in result.error.lower()


def test_normalize_error_never_leaks_prompt():
    err = RuntimeError("connect fails while smuggling SECRET_KEY=abc123")
    message = _normalize_error(err)
    assert "SECRET_KEY" not in message
    assert "abc123" not in message
    assert message == "LLM provider execution error."


def test_normalize_error_maps_http_statuses():
    assert "rate limit" in _normalize_error(
        litellm.RateLimitError(message="slow down", model="gpt-test", llm_provider="openai")
    )

    class _ServerErr(Exception):
        status_code = 503

    assert "execution error" in _normalize_error(_ServerErr())

    class _RateLimitedErr(Exception):
        status_code = 429

    assert "rejected (HTTP 429)" in _normalize_error(_RateLimitedErr())


@pytest.mark.asyncio
async def test_api_key_never_leaked_to_error(monkeypatch, provider):
    async def failing_acompletion(**kwargs):
        raise ConnectionError("provider unreachable")

    monkeypatch.setattr(litellm, "acompletion", failing_acompletion)

    result = await provider.execute(
        "prompt", {"model": "gpt-test", "api_key": "sk-SUPERSECRET123"}
    )

    assert result.error is not None
    assert "sk-SUPERSECRET123" not in result.error
    assert "gpt-test" not in result.error
