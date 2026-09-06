import asyncio
import time
from typing import Any, Dict, Optional

import litellm

from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse
from app.core.config import settings
from app.core.logging import get_logger

litellm.suppress_debug_info = True

logger = get_logger("provider.litellm")

# Errors considered transient: safe to retry with backoff.
_TRANSIENT_ERRORS = (
    litellm.Timeout,
    litellm.RateLimitError,
    litellm.InternalServerError,
    litellm.APIError,
    ConnectionError,
    TimeoutError,
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, _TRANSIENT_ERRORS):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    if isinstance(status, int) and status == 429:
        return True
    return False


def _normalize_error(exc: Exception) -> str:
    """Map provider exceptions to clean, log-safe messages (no credentials)."""
    if isinstance(exc, litellm.AuthenticationError):
        return "LLM provider authentication failed. Check provider API keys."
    if isinstance(exc, litellm.RateLimitError):
        return "LLM provider rate limit exceeded."
    if isinstance(exc, litellm.Timeout):
        return "LLM provider request timed out."
    if isinstance(exc, litellm.BadRequestError):
        return f"LLM provider rejected the request: {type(exc).__name__}"
    if isinstance(exc, litellm.ContextWindowExceededError):
        return "LLM provider context window exceeded."
    if isinstance(exc, litellm.NotFoundError):
        return f"LLM provider model not found: {getattr(exc, 'message', 'unknown model')}"

    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500:
        return f"LLM provider request rejected (HTTP {status})."
    return "LLM provider execution error."


class LiteLLMTargetProvider(TargetProvider):
    """
    Production-grade TargetProvider powered by LiteLLM.
    Supports 100+ model providers.

    Resilience features:
      * request timeout
      * retries with exponential backoff for transient failures
      * provider error normalization (never leaks raw credentials/prompts)
      * configurable retry count (``LLM_MAX_RETRIES``)
    Permanent errors (auth, bad request, missing model, context window) are
    never retried.
    """

    def __init__(
        self,
        default_model: str = "gpt-4o",
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        self.default_model = default_model
        self.max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
        self.timeout = settings.LLM_TIMEOUT_SECONDS if timeout is None else timeout

    async def execute(self, prompt: str, config: Dict[str, Any]) -> TargetResponse:
        start_time = time.perf_counter()

        model = config.get("model", self.default_model)
        temperature = config.get("temperature", 0.7)
        api_key = config.get("api_key")

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": self.timeout,
        }

        if api_key:
            kwargs["api_key"] = api_key

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await litellm.acompletion(**kwargs)

                latency = (time.perf_counter() - start_time) * 1000.0
                latency = max(latency, 0.001)

                content = response.choices[0].message.content or ""

                usage = getattr(response, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                total_tokens = getattr(usage, "total_tokens", 0) if usage else 0

                logger.info(
                    "LLM call succeeded",
                    extra={
                        "event_name": "provider.llm_call",
                        "provider": "litellm",
                        "model": model,
                        "latency_ms": round(latency, 2),
                        "prompt_tokens": prompt_tokens or 0,
                        "completion_tokens": completion_tokens or 0,
                        "total_tokens": total_tokens or 0,
                    },
                )

                return TargetResponse(
                    response_text=content,
                    latency_ms=latency,
                    prompt_tokens=prompt_tokens or 0,
                    completion_tokens=completion_tokens or 0,
                    total_tokens=total_tokens or 0,
                )

            except Exception as exc:  # noqa: BLE001 - normalized below
                last_error = exc
                transient = _is_transient(exc)

                logger.warning(
                    "LLM provider error",
                    extra={
                        "event_name": "provider.error",
                        "provider": "litellm",
                        "model": model,
                        "attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                        "transient": transient,
                    },
                )

                if not transient or attempt >= self.max_retries:
                    break

                delay = settings.LLM_RETRY_BACKOFF_SECONDS * (2**attempt)
                await asyncio.sleep(delay)

        latency = (time.perf_counter() - start_time) * 1000.0
        latency = max(latency, 0.001)

        return TargetResponse(
            response_text="",
            latency_ms=latency,
            error=_normalize_error(last_error) if last_error else "LLM provider error.",
        )
