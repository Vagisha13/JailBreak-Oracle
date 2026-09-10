from app.targets.base import TargetProvider
from app.targets.mock import (
    MockAttackerProvider,
    MockDefenderProvider,
    MockEvaluatorProvider,
    MockTargetProvider,
    MockVerifierProvider,
)
from app.targets.litellm import LiteLLMTargetProvider


class TargetFactory:
    """Factory to instantiate target execution providers.

    ``role`` selects the deterministic agent mock when ``provider_type ==
    "mock"`` so the composition root can wire attacker/evaluator/verifier/
    defender through the exact same path as production providers. Default
    (``"target"``) preserves the original plain-text mock target behavior.
    """

    @staticmethod
    def get_provider(
        provider_type: str,
        default_model: str = "gpt-4o",
        role: str = "target",
    ) -> TargetProvider:
        provider_lower = provider_type.lower()

        if provider_lower == "mock":
            if role == "attacker":
                return MockAttackerProvider()
            if role == "evaluator":
                return MockEvaluatorProvider()
            if role == "verifier":
                return MockVerifierProvider()
            if role == "defender":
                return MockDefenderProvider()
            return MockTargetProvider()

        elif provider_lower in [
            "litellm",
            "openai",
            "anthropic",
            "deepseek",
            "ollama",
            "custom",
        ]:
            return LiteLLMTargetProvider(default_model=default_model)

        else:
            raise ValueError(f"Unsupported target provider type: '{provider_type}'")
