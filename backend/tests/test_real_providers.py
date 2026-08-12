import pytest
from unittest.mock import patch, AsyncMock
from app.targets.factory import TargetFactory
from app.targets.litellm import LiteLLMTargetProvider
from app.targets.embeddings import OpenAIEmbeddingProvider


@pytest.mark.asyncio
async def test_target_factory_real_providers():
    provider = TargetFactory.get_provider("openai", default_model="gpt-4o")
    assert isinstance(provider, LiteLLMTargetProvider)

    anthropic_provider = TargetFactory.get_provider(
        "anthropic", default_model="claude-3-5-sonnet-20240620"
    )
    assert isinstance(anthropic_provider, LiteLLMTargetProvider)


@pytest.mark.asyncio
@patch("litellm.acompletion")
async def test_litellm_provider_execution_mocked(mock_acompletion):
    # Mock LiteLLM Completion Response
    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content="Mocked LLM compliance text"))
    ]
    mock_response.usage = AsyncMock(
        prompt_tokens=15, completion_tokens=10, total_tokens=25
    )
    mock_acompletion.return_value = mock_response

    provider = LiteLLMTargetProvider(default_model="gpt-4o")
    result = await provider.execute(
        prompt="Tell me a story", config={"model": "gpt-4o", "temperature": 0.5}
    )

    assert result.error is None
    assert result.response_text == "Mocked LLM compliance text"
    assert result.prompt_tokens == 15
    assert result.completion_tokens == 10
    assert result.total_tokens == 25
    assert result.latency_ms > 0


@pytest.mark.asyncio
@patch("litellm.aembedding")
async def test_openai_embedding_provider_mocked(mock_aembedding):
    # Mock LiteLLM Embedding Response
    dummy_vector = [0.1] * 1536
    mock_aembedding.return_value = AsyncMock(data=[{"embedding": dummy_vector}])

    provider = OpenAIEmbeddingProvider()
    vector = await provider.generate_embedding("Test prompt embedding")

    assert len(vector) == 1536
    assert vector[0] == 0.1
