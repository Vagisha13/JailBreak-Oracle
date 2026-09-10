"""Configurable API rate limiting.

Supports two backends:
  * In-memory sliding-window (default; used in development/test and as a
    fail-open fallback when Redis is unreachable).
  * Redis fixed-window counter (used when ``REDIS_URL`` is configured) so that
    limits are shared across multiple application instances in production.

Redis is used only while the shared client (app.core.redis_client) reports it
healthy. Any live failure trips the shared circuit breaker and the request is
served from the in-memory backend — a Redis outage slows us down but never
returns 500s. Clients may briefly exceed shared limits during a degradation;
counter-side slack is the price of fail-open availability.

Client identity trusts ``X-Forwarded-For`` ONLY for peers listed in
``settings.TRUSTED_PROXIES`` (E-24). Without a configured proxy allowlist the
raw socket peer is used and spoofed forwarded headers are ignored.

Health checks and other cheap/no-risk endpoints are never rate-limited.
"""
import asyncio
import ipaddress
import json
import time
from typing import Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.redis_client import (
    circuit_open,
    forget_cached_client,
    get_redis_client,
    is_redis_configured,
)

# Public paths that are intentionally exempt from rate limiting.
_RATE_LIMIT_EXEMPT_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")

_active_limiter: dict = {"memory": None, "middleware": None}


class InMemoryRateLimiter:
    """Thread-safe-enough sliding window limiter for a single instance."""

    def __init__(self) -> None:
        self._hits: dict = {}

    async def check(self, key: str, limit: int, window: int) -> Tuple[bool, Optional[int]]:
        now = time.monotonic()
        timestamps = [t for t in self._hits.get(key, []) if now - t < window]
        if len(timestamps) >= limit:
            self._hits[key] = timestamps
            oldest = timestamps[0]
            retry_after = max(1, int(window - (now - oldest)))
            return False, retry_after
        timestamps.append(now)
        self._hits[key] = timestamps
        return True, None

    def reset(self) -> None:
        self._hits.clear()


class RedisRateLimiter:
    """Fixed-window Redis-backed limiter shared across instances.

    ``check`` returns ``None`` instead of raising when Redis fails mid-request;
    the middleware treats that as "degrade this request to in-memory".
    """

    def __init__(self, redis_client) -> None:
        self.redis = redis_client
        self._prefix = "oracle:ratelimit:"

    async def check(self, key: str, limit: int, window: int) -> Optional[Tuple[bool, Optional[int]]]:
        redis_key = f"{self._prefix}{key}"
        try:
            count = await self.redis.incr(redis_key)
            if count == 1:
                await self.redis.expire(redis_key, window)
            if count > limit:
                ttl = await self.redis.ttl(redis_key)
                return False, max(1, int(ttl) if ttl and ttl > 0 else 1)
            return True, None
        except Exception as exc:  # redis.exceptions.RedisError, ConnectionError, timeout...
            from app.core.redis_client import trip_circuit

            trip_circuit(type(exc).__name__)
            return None

    async def reset_global(self) -> None:
        """Drop every key this limiter owns (shared counters across instances)."""
        try:
            keys = [k async for k in self.redis.scan_iter(match=self._prefix + "*")]
            if keys:
                await self.redis.delete(*keys)
        except Exception:
            pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies per-bucket, per-client rate limits to API requests."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._redis_limiter: Optional[RedisRateLimiter] = None
        self._memory = InMemoryRateLimiter()
        _active_limiter["memory"] = self._memory
        _active_limiter["middleware"] = self

    def _get_limiter(self):
        if is_redis_configured():
            if self._redis_limiter is None:
                self._redis_limiter = RedisRateLimiter(get_redis_client())
            return self._redis_limiter
        return self._memory

    @staticmethod
    def _classify(request: Request) -> Optional[Tuple[str, int]]:
        path = request.url.path

        for exempt in _RATE_LIMIT_EXEMPT_PREFIXES:
            if path.startswith(exempt):
                return None

        if path.startswith("/api/v1/auth/"):
            return "auth", settings.AUTH_RATE_LIMIT_PER_MINUTE

        if path.startswith("/api/v1/vulnerabilities/verify"):
            return "llm", settings.LLM_RATE_LIMIT_PER_MINUTE

        if path.startswith("/api/v1/campaigns/start") or (
            path.startswith("/api/v1/campaigns")
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
        ):
            return "campaign", settings.CAMPAIGN_RATE_LIMIT_PER_MINUTE

        return "default", settings.RATE_LIMIT_PER_MINUTE

    @staticmethod
    def _is_trusted_proxy(peer: str, trusted: list[str]) -> bool:
        """True when the direct peer is in the trusted proxy allowlist."""
        if not peer or peer == "unknown":
            return False
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return False
        for entry in trusted:
            try:
                if "/" in entry:
                    if peer_ip in ipaddress.ip_network(entry, strict=False):
                        return True
                elif ipaddress.ip_address(entry) == peer_ip:
                    return True
            except ValueError:
                # Malformed allowlist entries are skipped, never fatal.
                continue
        return False

    @staticmethod
    def _client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        # Only honor a forwarded client IP when the connecting peer is a
        # configured trusted proxy — otherwise any client could spoof the header
        # to evade per-IP buckets (E-24).
        if RateLimitMiddleware._is_trusted_proxy(peer, settings.trusted_proxy_list):
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return peer

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        classified = self._classify(request)
        if classified is None:
            return await call_next(request)

        bucket, limit = classified
        if limit <= 0:
            return await call_next(request)

        limiter = self._get_limiter()
        key = f"{bucket}:{self._client_ip(request)}"
        checked = await limiter.check(key, limit, settings.RATE_LIMIT_WINDOW_SECONDS)

        if checked is None and limiter is not self._memory:
            # Redis failed mid-flight (circuit tripped). Fail open to memory for
            # this request rather than 500ing the API.
            checked = await self._memory.check(key, limit, settings.RATE_LIMIT_WINDOW_SECONDS)

        allowed, retry_after = checked
        if not allowed:
            retry_after = retry_after or settings.RATE_LIMIT_WINDOW_SECONDS
            body = json.dumps(
                {
                    "detail": "Rate limit exceeded. Please retry later.",
                    "code": "rate_limit_exceeded",
                    "retry_after": retry_after,
                }
            )
            return Response(
                status_code=429,
                content=body,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    async def reset(self) -> None:
        """Reset counters on both backends (used by tests)."""
        self._memory.reset()
        if self._redis_limiter is not None:
            await self._redis_limiter.reset_global()
            self._redis_limiter = None


def _flush_redis_rate_limit_keys() -> None:
    """Synchronously delete every rate-limit counter key owned by Redis.

    Uses a short-lived sync client so the async limiter's lifecycle is
    untouched. Safe to call from test helpers and admin utilities: when Redis
    is unreachable the in-memory backend still governs the current request and
    a later probe retrips the shared circuit. Best-effort by design.
    """
    url = settings.REDIS_URL
    if not url:
        return
    try:
        import redis as sync_redis  # sync driver ships with redis-py

        client = sync_redis.Redis.from_url(
            url,
            socket_connect_timeout=1.0,
            socket_timeout=2.0,
        )
        try:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor, match="oracle:ratelimit:*", count=200)
                if keys:
                    client.delete(*keys)
                if cursor == 0:
                    break
        finally:
            client.close()
    except Exception:  # pragma: no cover - connectivity is best-effort here
        pass


async def _aclose_client(client) -> None:
    """Best-effort ``aclose`` so the coroutine runs under an active loop."""
    try:
        await client.aclose()
    except Exception:  # pragma: no cover - loop may already be tearing down
        pass


def _dispose_redis_limiter() -> None:
    """Drop the cached Redis limiter, closing its async client first.

    redis.asyncio pools bind their connections to a specific event loop; the
    test suite runs each async case in its own loop, so a stale client must be
    closed (never garbage-collected) whenever limiter state is reset. Closing
    is scheduled on the currently running loop when one exists.
    """
    middleware = _active_limiter.get("middleware")
    if middleware is None:
        return
    limiter = getattr(middleware, "_redis_limiter", None)
    if limiter is None:
        return
    middleware._redis_limiter = None
    client = limiter.redis
    if client is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(_aclose_client(client))


def reset_rate_limiter() -> None:
    """Reset rate-limit state across every backend (tests/admin utilities).

    Clears the in-memory counters, drops the middleware's cached Redis limiter
    (closing its pooled connections), deletes the shared Redis counters, and
    forgives the shared failure circuit so the next request re-probes Redis.
    """
    memory = _active_limiter.get("memory")
    if memory is not None and hasattr(memory, "reset"):
        memory.reset()
    _dispose_redis_limiter()
    _flush_redis_rate_limit_keys()
    forget_cached_client()


def circuit_open_state() -> bool:
    """Expose the shared circuit state for diagnostics."""
    return circuit_open()
