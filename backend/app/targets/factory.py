from app.targets.base import TargetProvider
from app.targets.mock import MockTargetProvider
from app.targets.litellm import LiteLLMTargetProvider


class TargetFactory:
    """Factory to instantiate target execution providers."""

    @staticmethod
    def get_provider(
        provider_type: str, default_model: str = "gpt-4o"
    ) -> TargetProvider:
        provider_lower = provider_type.lower()

        if provider_lower == "mock":
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
