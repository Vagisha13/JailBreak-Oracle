"""Redis-backed campaign job queue.

The queue is intentionally small: a single Redis list per deployment.
Enqueue/dequeue return ``None``/``False`` when Redis is not configured or has
tripped its failure circuit, so callers can transparently fall back to
in-process execution (development/tests/outage).
"""
import uuid
from typing import Any, Optional

from app.core.logging import get_logger
from app.core.redis_client import get_redis_client

logger = get_logger("queue")

JOB_QUEUE_KEY = "oracle:campaign:jobs"

# Test-injection ports (kept for backwards compatibility with the original
# module-level client). Production code never writes these - the real path goes
# through ``get_redis_client`` - but tests replace ``_redis_client`` with a fake
# to drive queue behavior without touching Redis.
_redis_client: Optional[Any] = None
_redis_attempted = False


async def enqueue_campaign(
    experiment_id: uuid.UUID, redis_client: Optional[Any] = None
) -> bool:
    """Push a campaign job onto the queue. Returns False when Redis is unavailable."""
    client = redis_client or _redis_client or get_redis_client()
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
    client = redis_client or _redis_client or get_redis_client()
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
