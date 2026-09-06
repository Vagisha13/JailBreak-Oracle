"""Configurable API rate limiting.

Supports two backends:
  * In-memory sliding-window (default; used in development/test and as a
    fail-open fallback when Redis is unreachable).
  * Redis fixed-window counter (used when ``REDIS_URL`` is configured) so that
    limits are shared across multiple application instances in production.

Health checks and other cheap/no-risk endpoints are never rate-limited.
"""
import json
import time
from typing import Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

# Public paths that are intentionally exempt from rate limiting.
_RATE_LIMIT_EXEMPT_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")

_active_limiter: dict = {"instance": None}


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
    """Fixed-window Redis-backed limiter shared across instances."""

    def __init__(self, redis_client) -> None:
        self.redis = redis_client
        self._prefix = "oracle:ratelimit:"

    async def check(self, key: str, limit: int, window: int) -> Tuple[bool, Optional[int]]:
        redis_key = f"{self._prefix}{key}"
        count = await self.redis.incr(redis_key)
        if count == 1:
            await self.redis.expire(redis_key, window)
        if count > limit:
            ttl = await self.redis.ttl(redis_key)
            return False, max(1, int(ttl) if ttl and ttl > 0 else 1)
        return True, None

    def reset(self) -> None:
        pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies per-bucket, per-client rate limits to API requests."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._limiter: Optional[object] = None
        self._limiter_attempted = False

    def _get_limiter(self):
        if self._limiter is None and not self._limiter_attempted:
            limiter = InMemoryRateLimiter()
            if settings.REDIS_URL:
                try:
                    import redis.asyncio as aioredis  # type: ignore

                    client = aioredis.from_url(
                        settings.REDIS_URL, socket_connect_timeout=1
                    )
                    limiter = RedisRateLimiter(client)
                except Exception:
                    # Fail open: fall back to in-memory limiting if Redis is down.
                    limiter = InMemoryRateLimiter()
            self._limiter = limiter
            self._limiter_attempted = True
            _active_limiter["instance"] = limiter
        return self._limiter

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
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

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
        allowed, retry_after = await limiter.check(
            key, limit, settings.RATE_LIMIT_WINDOW_SECONDS
        )

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

        if hasattr(limiter, "reset"):
            request.app.state.rate_limiter = limiter

        response = await call_next(request)
        return response


def reset_rate_limiter() -> None:
    """Reset the active limiter (used by tests)."""
    instance = _active_limiter.get("instance")
    if instance is not None and hasattr(instance, "reset"):
        instance.reset()
