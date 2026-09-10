from abc import ABC, abstractmethod
from app.schemas.target import TargetResponse


class TargetProvider(ABC):
    """
    Abstract Base Class for all target LLMs.
    Every new target provider must implement this interface.
    """

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: list[dict] | None = None,
    ) -> TargetResponse:
        """
        Executes a prompt against the target LLM.

        Args:
            prompt: The adversarial payload or test case. For multi-turn
                conversations this is the *current* turn's payload; the full
                exchange is passed via ``messages``.
            config: Target-specific configuration (e.g., model name, API key,
                temperature).
            messages: Optional OpenAI-style ``[{role, content}]`` list
                representing the conversation so far (including the current
                prompt as the final user message). When ``None`` providers must
                behave exactly as before, treating ``prompt`` as a single-turn
                user message.

        Returns:
            TargetResponse object containing the result, latency, and token usage.
        """
        pass
