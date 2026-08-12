import time
import asyncio
from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse


class MockTargetProvider(TargetProvider):
    """
    A deterministic target provider for automated testing and CI/CD.
    Simulates network latency and conditional errors.
    """

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        start_time = time.perf_counter()

        # Simulate network latency
        await asyncio.sleep(0.05)

        latency_ms = (time.perf_counter() - start_time) * 1000

        # Simulate a provider error if "error" is in the prompt
        if "error" in prompt.lower():
            return TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error="Simulated provider connection error.",
            )

        return TargetResponse(
            response_text=f"I am a safe mock response to: {prompt[:20]}...",
            latency_ms=latency_ms,
            prompt_tokens=len(prompt) // 4,  # Rough estimation for mock
            completion_tokens=15,
            total_tokens=(len(prompt) // 4) + 15,
        )
