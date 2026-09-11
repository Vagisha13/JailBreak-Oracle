"""PostgreSQL-backed durable campaign job queue.

Replaces the Redis list queue. Campaign jobs are rows in ``campaign_jobs``:
the same database that already owns campaign/experiment state, so a worker
crash can never lose a job and multiple workers are safe by construction.

Queue contract:
    API  ->  enqueue_campaign          (insert PENDING job; idempotent)
    worker -> claim_next_job           (PENDING -> RUNNING, atomic)
             execute
             mark_job_completed        (RUNNING -> COMPLETED)
             fail_job                  (RUNNING -> PENDING+backoff, or FAILED)

Claiming is atomic on both PostgreSQL and SQLite (dev/tests):
  * PostgreSQL: ``SELECT ... FOR UPDATE SKIP LOCKED`` takes an exclusive row
    lock so concurrent workers each see disjoint candidates; the follow-up
    ``UPDATE ... WHERE id = ? AND status = 'PENDING'`` CAS is a double guard.
  * SQLite (dev/tests) has no row locking, but the single-writer database plus
    the status CAS gives the same "only one worker flips the row" guarantee.

The queue is scheduling state only. Who may actually *execute* a campaign is
decided by the existing PostgreSQL experiment lease (``app.services.lease``),
which this module does not touch.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import CampaignJob

logger = get_logger("queue")

# Job lifecycle statuses (mirrors the campaign status vocabulary).
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_COMPLETED = "COMPLETED"
JOB_FAILED = "FAILED"

# Returned by ``fail_job`` so the worker knows whether to retry.
RETRYABLE = "RETRYABLE"

_MAX_ERROR_LENGTH = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """Normalize naive DB timestamps to UTC (SQLite drivers drop tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _truncate(error: object) -> str:
    """Store a safe, bounded failure reason (secrets are redacted upstream)."""
    message = str(error) if error is not None else ""
    return message[: _MAX_ERROR_LENGTH]


async def enqueue_campaign(experiment_id: uuid.UUID) -> bool:
    """Create a pending job for ``experiment_id`` (idempotent).

    At most one active (PENDING/RUNNING) job may exist per experiment thanks to
    the partial unique index, so duplicate enqueues collapse to a no-op while a
    terminal job still allows a later, fresh re-enqueue. Returns True when the
    job is queued or was already queued.
    """
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(CampaignJob.id).where(
                    CampaignJob.experiment_id == experiment_id,
                    CampaignJob.status.in_([JOB_PENDING, JOB_RUNNING]),
                )
            )
        ).scalars().first()
        if existing is not None:
            logger.info(
                "Campaign already queued",
                extra={"event_name": "campaign.already_queued", "campaign_id": str(experiment_id)},
            )
            return True

        session.add(CampaignJob(experiment_id=experiment_id, status=JOB_PENDING))
        try:
            await session.commit()
        except IntegrityError:
            # Lost a concurrent enqueue race: the winner's active row now exists.
            await session.rollback()
        logger.info(
            "Campaign enqueued",
            extra={
                "event_name": "campaign.enqueued",
                "campaign_id": str(experiment_id),
                "queue": "postgresql:campaign_jobs",
            },
        )
        return True


async def claim_next_job() -> Optional[CampaignJob]:
    """Atomically claim the next eligible pending job.

    A job is eligible when it is ``PENDING`` and its ``available_at`` has
    passed. The row is locked (``FOR UPDATE SKIP LOCKED`` on PostgreSQL) and
    flipped PENDING -> RUNNING in the same transaction, with ``attempts``
    incremented so a retried job advances its budget. Returns the claimed job
    (callers may own and execute it) or None when the queue is empty.
    """
    now = _now()
    async with AsyncSessionLocal() as session:
        candidate = (
            await session.execute(
                select(CampaignJob)
                .where(
                    CampaignJob.status == JOB_PENDING,
                    CampaignJob.available_at <= now,
                )
                .order_by(CampaignJob.available_at, CampaignJob.created_at, CampaignJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalars().first()
        if candidate is None:
            await session.rollback()
            return None

        result = await session.execute(
            sa_update(CampaignJob)
            .where(CampaignJob.id == candidate.id, CampaignJob.status == JOB_PENDING)
            .values(
                status=JOB_RUNNING,
                attempts=CampaignJob.attempts + 1,
                available_at=CampaignJob.available_at,
                started_at=now,
                completed_at=None,
                heartbeat_at=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            # Lost the CAS (another worker claimed it first on a dialect without
            # row locking). Move on — never execute an already-owned job.
            await session.rollback()
            return None
        await session.commit()
        await session.refresh(candidate)
        logger.info(
            "Campaign job claimed",
            extra={
                "event_name": "campaign.job_claimed",
                "job_id": str(candidate.id),
                "campaign_id": str(candidate.experiment_id),
                "attempt": candidate.attempts,
            },
        )
        return candidate


async def mark_job_completed(
    job_id: uuid.UUID, status: str = JOB_COMPLETED, note: Optional[str] = None
) -> bool:
    """Terminate a claimed job as COMPLETED (or, defensively, FAILED).

    ``note`` (e.g. the SKIPPED reason) is persisted in ``last_error`` so the
    queue ledger stays queryable. Only the RUNNING owner transitions the row.
    """
    now = _now()
    values = {
        "status": status,
        "completed_at": now,
        "heartbeat_at": now,
        "updated_at": now,
    }
    if note is not None:
        values["last_error"] = _truncate(note)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa_update(CampaignJob)
            .where(CampaignJob.id == job_id, CampaignJob.status == JOB_RUNNING)
            .values(**values)
        )
        await session.commit()
        return result.rowcount == 1


async def fail_job(
    job_id: uuid.UUID,
    error: object,
    max_attempts: Optional[int] = None,
    retry_backoff_seconds: Optional[float] = None,
    now: Optional[datetime] = None,
) -> str:
    """Record an execution failure on a claimed job.

    While ``attempts < max_attempts`` the job returns to PENDING with
    ``available_at`` pushed into the future (+ backoff * attempts): retries are
    bounded and cooled-down, and the persisted ``last_error`` explains why.
    Once the budget is exhausted the job is terminal FAILED.

    Returns :data:`RETRYABLE` (the worker should reset the experiment so the
    next claim can re-execute) or :data:`JOB_FAILED` (terminal).
    """
    max_attempts = max_attempts or settings.CAMPAIGN_JOB_MAX_ATTEMPTS
    backoff = (
        retry_backoff_seconds
        if retry_backoff_seconds is not None
        else settings.CAMPAIGN_JOB_RETRY_BACKOFF_SECONDS
    )
    now = now or _now()
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == job_id))
        ).scalars().first()
        if job is None:
            return JOB_FAILED

        job.last_error = _truncate(error)
        job.heartbeat_at = now
        job.updated_at = now
        if job.attempts < max_attempts:
            job.status = JOB_PENDING
            job.completed_at = None
            job.available_at = now + timedelta(seconds=backoff * job.attempts)
            await session.commit()
            logger.info(
                "Campaign job scheduled for retry",
                extra={
                    "event_name": "campaign.job_retry",
                    "job_id": str(job_id),
                    "campaign_id": str(job.experiment_id),
                    "attempt": job.attempts,
                    "max_attempts": max_attempts,
                    "available_at": job.available_at.isoformat(),
                },
            )
            return RETRYABLE

        job.status = JOB_FAILED
        job.completed_at = now
        await session.commit()
        logger.warning(
            "Campaign job failed permanently",
            extra={
                "event_name": "campaign.job_failed",
                "job_id": str(job_id),
                "campaign_id": str(job.experiment_id),
                "attempt": job.attempts,
                "error_type": type(error).__name__,
            },
        )
        return JOB_FAILED


async def touch_job(job_id: uuid.UUID) -> None:
    """Worker liveness heartbeat on a claimed job. Best-effort."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_update(CampaignJob)
                .where(CampaignJob.id == job_id, CampaignJob.status == JOB_RUNNING)
                .values(heartbeat_at=_now(), updated_at=_now())
            )
            await session.commit()
    except Exception:
        logger.warning(
            "Failed to touch campaign job heartbeat",
            extra={"event_name": "campaign.job_heartbeat_failed", "job_id": str(job_id)},
        )


async def recover_stale_jobs(
    max_age_seconds: Optional[int] = None, now: Optional[datetime] = None
) -> list[uuid.UUID]:
    """Re-admit RUNNING jobs whose worker died without finishing.

    Staleness keys on the job's liveness heartbeat (falling back to
    ``started_at``), never on row age: a legitimately long-running campaign
    bumps its heartbeat every round and is never swept. Crashed jobs return to
    PENDING with ``available_at`` reset so any worker may claim them again.
    """
    max_age_seconds = max_age_seconds or settings.CAMPAIGN_TIMEOUT_SECONDS
    now = now or _now()
    cutoff = now - timedelta(seconds=max_age_seconds)
    resumed: list[uuid.UUID] = []

    async with AsyncSessionLocal() as session:
        running = (
            await session.execute(
                select(CampaignJob).where(CampaignJob.status == JOB_RUNNING)
            )
        ).scalars().all()
        for job in running:
            activity = job.heartbeat_at or job.started_at
            if activity is None or _aware(activity) < cutoff:
                job.status = JOB_PENDING
                job.started_at = None
                job.available_at = now
                job.updated_at = now
                resumed.append(job.id)
        if resumed:
            await session.commit()
            logger.info(
                "Recovered stale campaign jobs",
                extra={
                    "event_name": "campaign.job_recovery",
                    "num_recovered": len(resumed),
                },
            )
    return resumed


# Kept import-compatible with callers that referenced the old module-level key.
JOB_QUEUE_KEY = "postgresql:campaign_jobs"

__all__ = [
    "CampaignJob",
    "JOB_PENDING",
    "JOB_RUNNING",
    "JOB_COMPLETED",
    "JOB_FAILED",
    "RETRYABLE",
    "JOB_QUEUE_KEY",
    "enqueue_campaign",
    "claim_next_job",
    "mark_job_completed",
    "fail_job",
    "touch_job",
    "recover_stale_jobs",
]
