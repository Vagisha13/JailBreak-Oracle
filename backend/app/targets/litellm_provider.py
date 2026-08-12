import time
import litellm
from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse


class LiteLLMTarget(TargetProvider):
    """
    A universal target provider utilizing LiteLLM.
    Supports OpenAI, Anthropic, Google, HuggingFace, etc.
    """

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        start_time = time.perf_counter()

        model_name = config.get("model", "gpt-3.5-turbo")
        api_key = config.get("api_key")
        temperature = config.get("temperature", 0.7)

        try:
            # LiteLLM's unified async completion
            response = await litellm.acompletion(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                temperature=temperature,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000
            usage = response.usage

            return TargetResponse(
                response_text=response.choices[0].message.content,
                latency_ms=latency_ms,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            )

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error=f"LiteLLM Provider Error: {str(e)}",
            )
