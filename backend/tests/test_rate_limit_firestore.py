"""End-to-end rate limiting over the Firestore store (fake adapter).

These tests drive the fully-wired app middleware with Firebase "configured"
(credentials faked) and a FakeFirestore-backed store injected through the live
middleware's ``store_builder``.
"""
import pytest

from app.core.config import settings
from app.core.firestore_store import FakeFirestore


@pytest.fixture
def firestore_rate_limit(monkeypatch):
    """Enable rate limiting with the Firestore store and reset it per test."""
    from app.core.config import Settings
    from app.core.ratelimit import _active_limiter

    # Make settings look like a configured Firebase deployment. dispatch reads
    # the property lazily per request (no real Firebase init is performed).
    monkeypatch.setattr(
        Settings, "firebase_configured", property(lambda self: True)
    )
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60)

    store = FakeFirestore()
    # Bare fake: it already exposes the store protocol (run_transaction with
    # txn.get/txn.set) that FirestoreRateLimiter._compute expects.
    middleware = _active_limiter.get("middleware")
    assert middleware is not None, "rate-limit middleware not built"
    monkeypatch.setattr(middleware, "_store_builder", lambda: store)
    middleware._firestore_limiters = {}

    yield store

    middleware._firestore_limiters = {}


def _live_middleware():
    from app.core.ratelimit import _active_limiter

    middleware = _active_limiter.get("middleware")
    assert middleware is not None
    return middleware


@pytest.mark.asyncio
async def test_firestore_unavailable_fails_closed_with_503(
    api_client, firestore_rate_limit, monkeypatch
):
    """With Firebase 'configured' but the store failing, requests fail CLOSED
    with a 503 rate_limit_unavailable rather than passing through."""
    class _UnavailableStore:
        """Minimal store: every transaction raises (Firestore unreachable)."""

        def run_transaction(self, fn):
            raise RuntimeError("connection refused")

        def reset_all(self):
            pass

    monkeypatch.setattr(_live_middleware(), "_store_builder", _UnavailableStore)
    _live_middleware()._firestore_limiters = {}

    resp = await api_client.post(
        "/api/v1/auth/login",
        data={"username": "user@oracle.sec", "password": "wrongpassword"},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "rate_limit_unavailable"


@pytest.mark.asyncio
async def test_health_is_exempt_under_firestore(
    api_client, firestore_rate_limit
):
    for _ in range(5):
        resp = await api_client.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_firestore_limit_across_multiple_requests(
    api_client, firestore_rate_limit
):
    """A single auth bucket over the Firestore store: two allowed, then 429."""
    statuses = []
    for i in range(3):
        resp = await api_client.post(
            "/api/v1/auth/login",
            data={"username": f"user{i}@oracle.sec", "password": "wrongpassword"},
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            assert resp.json()["code"] == "rate_limit_exceeded"
            assert resp.headers.get("retry-after")

    assert statuses == [401, 401, 429]
    assert len(firestore_rate_limit._documents) == 1  # auth bucket stored


@pytest.mark.asyncio
async def test_reset_recovers_after_firestore_limit(
    api_client, firestore_rate_limit
):
    store = firestore_rate_limit
    statuses = []
    for i in range(3):
        resp = await api_client.post(
            "/api/v1/auth/login",
            data={"username": f"user{i}@oracle.sec", "password": "wrongpassword"},
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert statuses == [401, 401, 429]

    store.reset_all()
    _live_middleware()._firestore_limiters = {}  # drop stale limiter/store

    resp = await api_client.post(
        "/api/v1/auth/login",
        data={"username": f"user{len(statuses)}@oracle.sec", "password": "wrongpassword"},
    )
    assert resp.status_code == 401, resp.text
