"""Redis-backed campaign job queue.

The queue is intentionally small: a single Redis list per deployment.
Enqueue/dequeue return ``None``/``False`` when Redis is not configured so callers
can transparently fall back to in-process execution (development/tests).
"""
import uuid
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("queue")

JOB_QUEUE_KEY = "oracle:campaign:jobs"

# The concrete client type derives from `redis.asyncio.Redis`, which is only
# imported lazily (see get_redis_connection). `Any` is used so callers can
# transparently treat an unconfigured/degraded Redis as "disabled".
_redis_client: Optional[Any] = None
_redis_attempted = False


async def get_redis_connection() -> Optional[Any]:
    """Lazily construct and cache a Redis client, or return None."""
    global _redis_client, _redis_attempted
    if _redis_attempted:
        return _redis_client
    _redis_attempted = True
    if not settings.REDIS_URL:
        logger.info("REDIS_URL not configured; Redis queue disabled")
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        _redis_client = client
        logger.info("Connected to Redis queue", extra={"event_name": "redis.connected"})
        return client
    except Exception as exc:
        logger.warning(
            "Failed to initialize Redis client; queue disabled",
            extra={"event_name": "redis.init_failed", "error_type": type(exc).__name__},
        )
        return None


async def enqueue_campaign(
    experiment_id: uuid.UUID, redis_client: Optional[Any] = None
) -> bool:
    """Push a campaign job onto the queue. Returns False when Redis is unavailable."""
    client = redis_client or await get_redis_connection()
    if client is None:
        return False
    try:
        await client.rpush(JOB_QUEUE_KEY, str(experiment_id))
        logger.info(
            "Campaign enqueued",
            extra={
                "event_name": "campaign.enqueued",
                "campaign_id": str(experiment_id),
            },
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to enqueue campaign",
            extra={
                "event_name": "campaign.enqueue_failed",
                "campaign_id": str(experiment_id),
                "error_type": type(exc).__name__,
            },
        )
        return False


async def dequeue_campaign(
    redis_client: Optional[Any] = None, timeout: int = 5
) -> Optional[uuid.UUID]:
    """Blocking-pop a campaign job from the queue, or None on timeout/unavailability."""
    client = redis_client or await get_redis_connection()
    if client is None:
        return None
    try:
        payload = await client.blpop(JOB_QUEUE_KEY, timeout=timeout)
    except Exception as exc:
        logger.warning(
            "Failed to dequeue campaign",
            extra={"event_name": "campaign.dequeue_failed", "error_type": type(exc).__name__},
        )
        return None
    if payload is None:
        return None
    try:
        return uuid.UUID(payload[1].decode())
    except (ValueError, AttributeError) as exc:
        logger.warning(
            "Invalid job payload on queue",
            extra={"event_name": "campaign.invalid_job", "error_type": type(exc).__name__},
        )
        return None
