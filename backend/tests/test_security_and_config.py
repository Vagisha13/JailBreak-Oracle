"""Application security and configuration hardening tests.

Covers CORS origin enforcement, security headers, production secrets
validation, and request-schema input validation.
"""
import uuid

import pytest

from app.core.config import settings
from tests.conftest import _auth


@pytest.mark.asyncio
async def test_cors_disallowed_origin_omits_allow_origin(api_client):
    resp = await api_client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_cors_allowed_origin_reflected(api_client):
    resp = await api_client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.asyncio
async def test_no_cors_header_without_origin(api_client):
    resp = await api_client.get("/health")
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_security_headers_present(api_client):
    resp = await api_client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_security_headers_can_be_disabled(api_client, monkeypatch):
    monkeypatch.setattr(settings, "SECURITY_ENABLE_HEADERS", False)
    resp = await api_client.get("/health")
    assert "x-content-type-options" not in resp.headers


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", None)
    monkeypatch.setattr(
        settings,
        "ASYNC_DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/oracle",
    )
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.validate_critical_secrets()


def test_production_requires_postgres(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "prod-secret")
    monkeypatch.setattr(settings, "ASYNC_DATABASE_URL", "sqlite+aiosqlite:///./oracle.db")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        settings.validate_critical_secrets()


def test_production_requires_redis(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "prod-secret")
    monkeypatch.setattr(
        settings,
        "ASYNC_DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/oracle",
    )
    monkeypatch.setattr(settings, "REDIS_URL", None)
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        settings.validate_critical_secrets()


def test_production_configuration_validates_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "prod-secret")
    monkeypatch.setattr(
        settings,
        "ASYNC_DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/oracle",
    )
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    settings.validate_critical_secrets()  # must not raise


@pytest.mark.asyncio
async def test_attack_budget_above_max_rejected(api_client, demo_env, mock_llm_providers):
    demo = await demo_env()
    resp = await api_client.post(
        "/api/v1/campaigns/start",
        headers=_auth(demo["token"]),
        json={
            "name": "Budget Exploit",
            "project_id": demo["project_id"],
            "target_id": demo["target_id"],
            "attack_budget": 99999,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_exploration_ratio_rejected(api_client, demo_env, mock_llm_providers):
    demo = await demo_env()
    resp = await api_client.post(
        "/api/v1/campaigns/start",
        headers=_auth(demo["token"]),
        json={
            "name": "Ratio Exploit",
            "project_id": demo["project_id"],
            "target_id": demo["target_id"],
            "exploration_ratio": 1.5,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_overlong_campaign_name_rejected(api_client, demo_env, mock_llm_providers):
    demo = await demo_env()
    resp = await api_client.post(
        "/api/v1/campaigns/start",
        headers=_auth(demo["token"]),
        json={
            "name": "x" * 500,
            "project_id": demo["project_id"],
            "target_id": demo["target_id"],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_malformed_uuid_path_rejected(api_client, demo_env):
    demo = await demo_env()
    resp = await api_client.get(
        "/api/v1/campaigns/not-a-uuid/status", headers=_auth(demo["token"])
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_campaign_returns_404_for_owner(api_client, demo_env):
    demo = await demo_env()
    resp = await api_client.get(
        f"/api/v1/campaigns/{uuid.uuid4()}/status", headers=_auth(demo["token"])
    )
    assert resp.status_code == 404
