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


@pytest.mark.asyncio
async def test_setup_demo_consumes_campaign_bucket(
    api_client, register_user, enable_rate_limit, monkeypatch
):
    creds = await register_user()
    # setup-demo itself is a POST under /campaigns => shares the campaign bucket.
    monkeypatch.setattr(settings, "CAMPAIGN_RATE_LIMIT_PER_MINUTE", 2)
    headers = _auth(creds["token"])
    for i in range(3):
        resp = await api_client.post("/api/v1/campaigns/setup-demo", headers=headers)
        if i < 2:
            assert resp.status_code == 200, resp.text
        else:
            assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_xff_spoofing_ignored_without_trusted_proxy(
    api_client, register_user, enable_rate_limit, monkeypatch
):
    """Without a TRUSTED_PROXIES allowlist, spoofed X-Forwarded-For must NOT
    create fresh per-IP buckets (E-24)."""
    creds = await register_user()
    monkeypatch.setattr(settings, "CAMPAIGN_RATE_LIMIT_PER_MINUTE", 2)

    statuses = []
    for spoofed_ip in ("203.0.113.7", "203.0.113.8", "203.0.113.9"):
        resp = await api_client.post(
            "/api/v1/campaigns/setup-demo",
            headers={**_auth(creds["token"]), "X-Forwarded-For": spoofed_ip},
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break

    # All three share the real peer's bucket: the third request is blocked even
    # though it used a "fresh" spoofed identity. If the header were honored,
    # every request would have passed.
    assert statuses == [200, 200, 429], statuses


@pytest.mark.asyncio
async def test_xff_honored_for_trusted_proxy(
    api_client, register_user, enable_rate_limit, monkeypatch
):
    """When the connecting peer is a configured trusted proxy, X-Forwarded-For
    is honored and each client gets its own bucket (E-24)."""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.setattr(settings, "CAMPAIGN_RATE_LIMIT_PER_MINUTE", 2)
    creds = await register_user()

    for spoofed_ip in ("203.0.113.7", "203.0.113.7", "203.0.113.9"):
        resp = await api_client.post(
            "/api/v1/campaigns/setup-demo",
            headers={**_auth(creds["token"]), "X-Forwarded-For": spoofed_ip},
        )
        # Two requests from 203.0.113.7 fit one bucket; the 203.0.113.9 request
        # is a fresh bucket. If XFF were ignored, the 3rd would 429.
        assert resp.status_code == 200, resp.text


def test_proxy_cidr_allowlist_matches():
    from app.core.ratelimit import RateLimitMiddleware

    assert RateLimitMiddleware._is_trusted_proxy(
        "10.1.2.3", ["10.0.0.0/8", "192.168.1.1"]
    )
    assert RateLimitMiddleware._is_trusted_proxy("192.168.1.1", ["192.168.1.1"])
    assert not RateLimitMiddleware._is_trusted_proxy("127.0.0.1", ["10.0.0.0/8"])
    assert not RateLimitMiddleware._is_trusted_proxy("not-an-ip", ["10.0.0.0/8"])
    assert not RateLimitMiddleware._is_trusted_proxy("127.0.0.1", [])
