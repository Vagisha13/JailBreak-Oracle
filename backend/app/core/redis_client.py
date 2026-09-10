"""Shared async Redis client for the queue and rate limiter.

Both consumers need Redis, and both must degrade gracefully when it is
unavailable (development, tests, or an outage). Keeping the connection pool and
its failure circuit in one module means:

  * One lazy connection is shared instead of each feature constructing its own.
  * After a Redis failure we stop probing for ``REDIS_RETRY_SECONDS`` (circuit
    breaker) instead of failing on every request, then transparently fall back
    to the in-memory / in-process degraded mode.
"""
import time
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("redis")

# Set by ``connect`` the first time Redis is usable:
_redis_client: Optional[Any] = None
_redis_attempted = False

# Monotonic time after which we may try Redis again (None = healthy/untried).
_circuit_open_until: Optional[float] = None


def _trip_circuit(error_type: str) -> None:
    global _circuit_open_until
    _circuit_open_until = time.monotonic() + settings.REDIS_RETRY_SECONDS
    logger.warning(
        "Redis unavailable; failing open for %ss",
        settings.REDIS_RETRY_SECONDS,
        extra={
            "event_name": "redis.circuit_tripped",
            "retry_seconds": settings.REDIS_RETRY_SECONDS,
            "error_type": error_type,
        },
    )


def trip_circuit(error_type: str) -> None:
    """Public alias so feature modules can trip the shared circuit."""
    _trip_circuit(error_type)


def forget_cached_client() -> None:
    """Drop the cached client + circuit without awaiting (sync-safe reset).

    Note: Prefer `await reset()` in async contexts to avoid leaking open connection pools.
    """
    global _redis_client, _redis_attempted, _circuit_open_until
    _redis_client = None
    _redis_attempted = False
    _circuit_open_until = None


def circuit_open() -> bool:
    """True when a recent Redis failure should make callers go degraded."""
    global _circuit_open_until
    if _circuit_open_until is None:
        return False
    if time.monotonic() < _circuit_open_until:
        return True
    _circuit_open_until = None
    return False


def is_redis_configured() -> bool:
    return bool(settings.REDIS_URL and not circuit_open())


def get_redis_client() -> Optional[Any]:
    """Return the cached shared Redis client, or None when unavailable.

    Construction is lazy and cached; it is separate from connectivity. A client
    may exist even while Redis is down (the URL was configured) — callers should
    still verify with ``redis_available`` before relying on it.
    """
    global _redis_client, _redis_attempted
    if _redis_attempted:
        return _redis_client
    _redis_attempted = True
    if not settings.REDIS_URL:
        logger.info("REDIS_URL not configured; Redis disabled")
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
            retry_on_timeout=False,
            health_check_interval=30,
        )
        _redis_client = client
        logger.info("Redis client created", extra={"event_name": "redis.client_created"})
        return client
    except Exception as exc:
        logger.warning(
            "Failed to initialize Redis client",
            extra={"event_name": "redis.init_failed", "error_type": type(exc).__name__},
        )
        _trip_circuit(type(exc).__name__)
        return None


async def redis_available() -> bool:
    """Best-effort live check that Redis accepts commands.

    Trips the circuit on failure so subsequent callers fail open cheaply. Safe
    to call per health probe, not per request.
    """
    if not settings.REDIS_URL:
        return False
    if circuit_open():
        return False
    client = get_redis_client()
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception as exc:
        _trip_circuit(type(exc).__name__)
        return False


async def check_health() -> dict:
    """Shape the ``/health`` response's ``redis`` section."""
    if not settings.REDIS_URL:
        return {"available": False, "reason": "REDIS_URL not set"}
    if await redis_available():
        return {"available": True}
    return {"available": False, "reason": "unreachable"}


async def reset() -> None:
    """Drop the cached client safely closing connection pools."""
    global _redis_client, _redis_attempted, _circuit_open_until
    if _redis_client is not None:
        try:
            if hasattr(_redis_client, "aclose"):
                await _redis_client.aclose()
            elif hasattr(_redis_client, "close"):
                await _redis_client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    _redis_client = None
    _redis_attempted = False
    _circuit_open_until = None