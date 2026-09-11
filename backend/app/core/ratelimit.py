"""Configurable API rate limiting (Firestore-backed; Redis replacement).

The shared rate limiter runs on Cloud Firestore through the Firebase Admin SDK
(server-side). PostgreSQL remains the system of record — Firestore stores only
ephemeral rate-limit counters in the ``rate_limits`` collection.

Semantics preserved from the previous Redis implementation:

  * limit / window   per-bucket per-window counts. Default bucket: 60/min;
                     auth: 10/min; campaign writes: 5/min; llm (verify): 30/min.
                     Window length: ``RATE_LIMIT_WINDOW_SECONDS`` (60s).
  * key format       ``{bucket}:{client_ip}`` hashed to a Firestore document id
                     ``rate_limits/{sha256 hash}``; the ``bucket`` field is kept
                     for scoped resets.
  * endpoint handling  ``_classify``: /auth -> auth, /vulnerabilities/verify ->
                     llm, /campaigns writes -> campaign, everything else ->
                     default. /health, /docs, /redoc, /openapi.json exempt.
  * user/IP handling ``_client_ip`` honors X-Forwarded-For only when the direct
                     peer is in ``settings.TRUSTED_PROXIES`` (E-24); otherwise
                     the raw socket peer is used so headers cannot be spoofed.
  * headers         429 + ``Retry-After`` with a JSON body carrying
                     ``code: "rate_limit_exceeded"`` and ``retry_after``.

Concurrency: every increment is a Firestore transaction (read latest counter,
increment, write). The transaction's optimistic-concurrency retry makes racing
requests safe, and the window reset (expired ``window_start`` -> fresh window)
is atomic under concurrency. No read-in-Python-write race exists.

Failure behavior: if Firestore is unreachable mid-request the request FAILS
CLOSED with a 503 ``rate_limit_unavailable`` — the distributed limiter is the
only enforcement, so an outage must never silently drop rate limiting. (The
previous Redis backend failed open to in-memory; that degraded mode is gone.)

Development/test default: when Firebase credentials are absent the limiter runs
per-instance in memory (single-process local dev only). Production configuration
is enforced at startup (see ``Settings.validate_critical_secrets``).
"""
import asyncio
import ipaddress
import json
import threading
import time
from typing import Any, Callable, Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("ratelimit")

# Public paths that are intentionally exempt from rate limiting.
_RATE_LIMIT_EXEMPT_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")

_active_limiter: dict = {"memory": None, "middleware": None}


class RateLimitUnavailableError(RuntimeError):
    """Raised when the shared (Firestore) rate limiter cannot be reached.

    The middleware converts this to a fail-closed 503 — never a silent
    degraded-mode pass through.
    """


class InMemoryRateLimiter:
    """Fixed-window per-instance limiter used only when Firebase is not
    configured (single-process development/tests). Same fixed-window semantics
    as the Firestore limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict = {}

    async def check(self, key: str, limit: int, window: int) -> Tuple[bool, Optional[int]]:
        now = time.monotonic()
        with self._lock:
            entry = self._hits.get(key)
            current = entry[0] if entry else 0
            window_start = entry[1] if entry else now
            if now - window_start >= window:
                current = 0
                window_start = now
            if current >= limit:
                return False, max(1, int(window - (now - window_start)))
            self._hits[key] = (current + 1, window_start)
            return True, None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class FirestoreRateLimiter:
    """Fixed-window shared limiter on Cloud Firestore.

    ``store`` exposes a concurrency-safe ``run_transaction(fn)``: for the real
    adapter this is a Cloud Firestore transaction (atomic read-modify-write with
    optimistic-concurrency retry); for tests it is an in-process stand-in with
    the same guarantees.
    """

    def __init__(self, store: Any, window: int, bucket: str) -> None:
        self._store = store
        self._window = window
        self._bucket = bucket

    async def check(self, key: str, limit: int, window: int) -> Tuple[bool, Optional[int]]:
        from app.core.firestore_store import doc_id_for

        doc_id = doc_id_for(self._bucket, key)

        def _compute(txn: Any) -> Tuple[bool, Optional[int]]:
            data = txn.get(doc_id) or {"count": 0, "window_start": 0}
            now = time.time()
            count = int(data["count"]) + 1
            window_start = float(data["window_start"])
            expired = now - window_start >= window
            if expired:
                window_start = now
                count = 1
            txn.set(
                doc_id,
                {
                    "count": count,
                    "window_start": window_start,
                    "expires_at": window_start + window,
                    "bucket": self._bucket,
                },
            )
            if count > limit:
                return False, max(1, int(window_start + window - now))
            return True, None

        try:
            return await asyncio.to_thread(self._store.run_transaction, _compute)
        except RateLimitUnavailableError:
            raise
        except Exception as exc:
            logger.warning(
                "Firestore rate limiter failed (failing closed)",
                extra={"event_name": "ratelimit.firestore_error",
                       "error_type": type(exc).__name__},
            )
            raise RateLimitUnavailableError(str(exc)) from exc

    async def reset_global(self) -> None:
        await asyncio.to_thread(self._store.reset_all)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies per-bucket, per-client rate limits to API requests."""

    def __init__(self, app, store_builder: Optional[Callable[[], Any]] = None) -> None:
        super().__init__(app)
        self._memory = InMemoryRateLimiter()
        self._store_builder = store_builder
        self._firestore_limiters: dict = {}
        _active_limiter["memory"] = self._memory
        _active_limiter["middleware"] = self

    def _get_limiter(self, bucket: str):
        # Lazy: without Firebase configured (dev/tests) we run in memory.
        if self._store_builder is None or not settings.firebase_configured:
            return self._memory
        if bucket in self._firestore_limiters:
            return self._firestore_limiters[bucket]
        store = self._store_builder()
        if store is None:
            raise RateLimitUnavailableError("Firestore is not available")
        limiter = FirestoreRateLimiter(
            store, window=settings.RATE_LIMIT_WINDOW_SECONDS, bucket=bucket
        )
        self._firestore_limiters[bucket] = limiter
        return limiter

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

        try:
            limiter = self._get_limiter(bucket)
        except RateLimitUnavailableError:
            return self._unavailable_response()

        key = f"{bucket}:{self._client_ip(request)}"
        try:
            checked = await limiter.check(key, limit, settings.RATE_LIMIT_WINDOW_SECONDS)
        except RateLimitUnavailableError:
            return self._unavailable_response()

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

    @staticmethod
    def _unavailable_response() -> Response:
        return Response(
            status_code=503,
            content=json.dumps(
                {
                    "detail": "Rate limiter unavailable. Please retry later.",
                    "code": "rate_limit_unavailable",
                }
            ),
            media_type="application/json",
        )

    async def reset(self) -> None:
        """Reset counters on both backends (used by tests)."""
        self._memory.reset()
        for bucket, limiter in self._firestore_limiters.items():
            if limiter is not None:
                await limiter.reset_global()
        self._firestore_limiters = {}


def build_store() -> Any:
    """Composition root for the Firestore rate-limit store.

    Returns None (middleware keeps dev in-memory mode) when Firebase is not
    configured; in production the configuration is enforced at startup, so a
    None here means a genuine init problem and requests fail closed.
    """
    from app.core.firebase import get_firestore

    db = get_firestore()
    if db is None:
        return None
    from app.core.firestore_store import FirestoreStore

    return FirestoreStore(db)


def reset_rate_limiter() -> None:
    """Reset rate-limit state across every backend (tests/admin utilities)."""
    memory = _active_limiter.get("memory")
    if memory is not None and hasattr(memory, "reset"):
        memory.reset()
    middleware = _active_limiter.get("middleware")
    if middleware is not None and hasattr(middleware, "reset"):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(middleware.reset())
