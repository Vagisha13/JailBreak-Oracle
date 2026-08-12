from abc import ABC, abstractmethod
from app.schemas.target import TargetResponse


class TargetProvider(ABC):
    """
    Abstract Base Class for all target LLMs.
    Every new target provider must implement this interface.
    """

    @abstractmethod
    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        """
        Executes a prompt against the target LLM.

        Args:
            prompt: The adversarial payload or test case.
            config: Target-specific configuration (e.g., model name, API key, temperature).

        Returns:
            TargetResponse object containing the result, latency, and token usage.
        """
        pass
