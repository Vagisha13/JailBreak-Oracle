import pytest
from app.targets.factory import TargetFactory
from app.schemas.target import TargetResponse


@pytest.mark.asyncio
async def test_mock_target_success():
    provider = TargetFactory.get_provider("mock")
    config = {}

    response = await provider.execute("Test prompt injection here", config)

    assert isinstance(response, TargetResponse)
    assert response.error is None
    assert "mock response" in response.response_text
    assert response.latency_ms > 0
    assert response.total_tokens > 0


@pytest.mark.asyncio
async def test_mock_target_simulated_error():
    provider = TargetFactory.get_provider("mock")
    config = {}

    response = await provider.execute("Please trigger an Error", config)

    assert isinstance(response, TargetResponse)
    assert response.error is not None
    assert "Simulated provider connection error" in response.error


def test_target_factory_unsupported_provider():
    with pytest.raises(ValueError) as excinfo:
        TargetFactory.get_provider("unsupported_provider_name")

    assert "Unsupported provider_type" in str(excinfo.value)
