"""Redis shared-client regression tests.

The app must never *silently* connect to an old/foreign Redis instance that
answers on the configured host and port (e.g. a stale "Redis for Windows"
service shadowing the Docker container's published 6379). These tests pin the
version gate and URL-scheme validation that back that guarantee.
"""
import pytest

from app.core import redis_client
from app.core.config import settings


class _FakeInfoRedis:
    """Async stand-in for the shared client implementing ``info("server")``."""

    def __init__(self, version: str):
        self._version = version

    async def info(self, section):  # noqa: ANN001 - mirrors redis.Client.info
        return {"redis_version": self._version}


def test_parse_version_handles_short_and_prefixed_values():
    assert redis_client.parse_version("7.4.10") == (7, 4, 10)
    assert redis_client.parse_version("3.0.504") == (3, 0, 504)
    assert redis_client.parse_version("7") == (7, 0, 0)
    assert redis_client.parse_version("7.4") == (7, 4, 0)
    assert redis_client.parse_version("") == (0, 0, 0)


def test_server_version_too_old_threshold_matches_settings_default():
    assert redis_client.server_version_too_old("3.0.504", "6.0.0") is True
    assert redis_client.server_version_too_old("5.0.14", "6.0.0") is True
    assert redis_client.server_version_too_old("6.0.0", "6.0.0") is False
    assert redis_client.server_version_too_old("6.2.7", "6.0.0") is False
    assert redis_client.server_version_too_old("7.4.10", "6.0.0") is False


@pytest.mark.asyncio
async def test_check_health_reports_available_and_version(monkeypatch):
    await redis_client.reset()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake:6379")
    monkeypatch.setattr(
        redis_client,
        "get_redis_client",
        lambda: _FakeInfoRedis("7.4.10"),
    )

    health = await redis_client.check_health()
    assert health["available"] is True
    assert health["redis_version"] == "7.4.10"


@pytest.mark.asyncio
async def test_check_health_rejects_incompatible_old_server(monkeypatch):
    await redis_client.reset()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake:6379")
    monkeypatch.setattr(
        redis_client,
        "get_redis_client",
        lambda: _FakeInfoRedis("3.0.504"),
    )

    health = await redis_client.check_health()
    assert health["available"] is False
    assert "3.0.504" in health["reason"]
    assert "incompatible" in health["reason"]


@pytest.mark.asyncio
async def test_check_health_unreachable_trips_circuit(monkeypatch):
    await redis_client.reset()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake:6379")

    class _BrokenInfoRedis:
        async def info(self, section):  # noqa: ANN001
            raise ConnectionError("boom")

    monkeypatch.setattr(
        redis_client,
        "get_redis_client",
        lambda: _BrokenInfoRedis(),
    )

    health = await redis_client.check_health()
    assert health["available"] is False
    assert health["reason"] == "unreachable"
    assert redis_client.circuit_open() is True


def test_production_rejects_non_redis_scheme(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "prod-secret")
    monkeypatch.setattr(
        settings,
        "ASYNC_DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/oracle",
    )
    monkeypatch.setattr(settings, "REDIS_URL", "http://not-redis:6379")
    with pytest.raises(RuntimeError, match=r"redis:// or rediss://"):
        settings.validate_critical_secrets()


def test_production_accepts_redis_and_rediss_schemes(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "prod-secret")
    monkeypatch.setattr(
        settings,
        "ASYNC_DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/oracle",
    )
    for url in ("redis://localhost:6379/0", "rediss://localhost:6380/0"):
        monkeypatch.setattr(settings, "REDIS_URL", url)
        settings.validate_critical_secrets()  # must not raise
