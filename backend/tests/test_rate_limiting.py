"""Rate-limiting tests.

Rate limiting is disabled globally in the test suite (see conftest); these
tests re-enable it, drive buckets over their limit, and reset limiter state so
other tests are never affected.
"""
import pytest

from app.core.config import settings
from app.core.ratelimit import reset_rate_limiter
from tests.conftest import _auth


@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60)
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.mark.asyncio
async def test_auth_bucket_returns_429_with_retry_after(api_client, enable_rate_limit):
    for i in range(1, 4):
        resp = await api_client.post(
            "/api/v1/auth/login",
            data={"username": f"user{i}@oracle.sec", "password": "wrongpassword"},
        )
        if i <= 2:
            assert resp.status_code == 401, resp.text
        else:
            assert resp.status_code == 429, resp.text
            assert resp.headers.get("retry-after"), "Retry-After header missing"
            body = resp.json()
            assert body["code"] == "rate_limit_exceeded"
            assert body["retry_after"] > 0


@pytest.mark.asyncio
async def test_health_is_exempt_from_rate_limit(api_client, enable_rate_limit):
    for _ in range(10):
        resp = await api_client.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reset_recovers_requests(api_client, register_user, enable_rate_limit):
    creds = await register_user()  # consumes one allowance (limit=2)
    await api_client.post(
        "/api/v1/auth/login",
        data={"username": creds["email"], "password": "wrongpassword"},
    )
    blocked = await api_client.post(
        "/api/v1/auth/login",
        data={"username": creds["email"], "password": "wrongpassword"},
    )
    assert blocked.status_code == 429

    reset_rate_limiter()

    allowed = await api_client.post(
        "/api/v1/auth/login",
        data={"username": creds["email"], "password": creds["password"]},
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_campaign_start_bucket_limited(
    api_client, demo_env, mock_llm_providers, enable_rate_limit, monkeypatch
):
    demo = await demo_env()
    # setup-demo itself is a POST under /campaigns => consumes 1 allowance.
    monkeypatch.setattr(settings, "CAMPAIGN_RATE_LIMIT_PER_MINUTE", 2)

    headers = _auth(demo["token"])
    payload = {
        "name": "Rate Limited",
        "project_id": demo["project_id"],
        "target_id": demo["target_id"],
    }
    first = await api_client.post("/api/v1/campaigns/start", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    second = await api_client.post("/api/v1/campaigns/start", headers=headers, json=payload)
    assert second.status_code == 429, second.text
