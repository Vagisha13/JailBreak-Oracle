import time
import litellm
from typing import Dict, Any

from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse

litellm.suppress_debug_info = True


class LiteLLMTargetProvider(TargetProvider):
    """
    Production-grade TargetProvider powered by LiteLLM.
    Supports 100+ model providers.
    """

    def __init__(self, default_model: str = "gpt-4o"):
        self.default_model = default_model

    async def execute(self, prompt: str, config: Dict[str, Any]) -> TargetResponse:

        start_time = time.perf_counter()

        model = config.get("model", self.default_model)
        temperature = config.get("temperature", 0.7)
        api_key = config.get("api_key")

        kwargs = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": temperature,
        }

        if api_key:
            kwargs["api_key"] = api_key

        try:
            response = await litellm.acompletion(**kwargs)

            # perf_counter() is intended for measuring elapsed time.
            latency = (time.perf_counter() - start_time) * 1000.0

            # Guarantee a positive latency for deterministic tests.
            latency = max(latency, 0.001)

            content = response.choices[0].message.content or ""

            usage = getattr(response, "usage", None)

            prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0

            completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

            total_tokens = getattr(usage, "total_tokens", 0) if usage else 0

            return TargetResponse(
                response_text=content,
                latency_ms=latency,
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                total_tokens=total_tokens or 0,
            )

        except Exception as exc:

            latency = (time.perf_counter() - start_time) * 1000.0
            latency = max(latency, 0.001)

            return TargetResponse(
                response_text="",
                latency_ms=latency,
                error=f"LiteLLM Provider Execution Error: {str(exc)}",
            )
