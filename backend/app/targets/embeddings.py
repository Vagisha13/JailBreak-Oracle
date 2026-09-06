from abc import ABC, abstractmethod
from typing import List, Optional
import litellm


class EmbeddingProvider(ABC):
    """Abstract interface for generating vector embeddings."""

    @abstractmethod
    async def generate_embedding(self, text: str) -> List[float]:
        pass


class MockEmbeddingProvider(EmbeddingProvider):
    """Generates a deterministic dummy vector for local testing without API costs."""

    async def generate_embedding(self, text: str) -> List[float]:
        val = float(len(text)) / 1000.0
        return [val] * 1536


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    Production embedding provider using OpenAI / LiteLLM embeddings API.
    Defaults to standard 1536-dimensional 'text-embedding-3-small'.
    """

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
    ):
        self.model_name = model_name
        self.api_key = api_key

    async def generate_embedding(self, text: str) -> List[float]:
        kwargs = {"model": self.model_name, "input": [text]}
        if self.api_key:
            kwargs["api_key"] = self.api_key

        try:
            response = await litellm.aembedding(**kwargs)
            return response.data[0]["embedding"]
        except Exception as exc:
            raise RuntimeError(f"Embedding generation failed: {str(exc)}")
