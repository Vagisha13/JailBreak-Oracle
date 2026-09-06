"""Redis-backed campaign worker.

Long-running campaign execution is handled by this persistent worker process
instead of FastAPI ``BackgroundTasks``. The worker reads campaign jobs from a
Redis queue, executes them, and updates the persisted campaign state:

    PENDING -> RUNNING -> COMPLETED
    PENDING -> RUNNING -> FAILED

Run with: ``python -m app.worker`` (or the ``worker`` service in Docker).

Guarantees:
  * A worker crash cannot leave a campaign permanently RUNNING: stale RUNNING
    campaigns are revereted to PENDING and re-queued on startup so they resume
    from persisted DB round state instead of restarting from scratch.
  * PENDING campaigns are re-queued after the grace period.
  * Provider failures mark the campaign FAILED (never stuck RUNNING).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.future import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment
from app.services.queue import dequeue_campaign, enqueue_campaign

logger = get_logger("worker")

PENDING_GRACE_SECONDS = 300


def _aware(dt: datetime) -> datetime:
    """Normalize naive DB timestamps to UTC (SQLite drivers drop tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def build_orchestrator():
    """Build the campaign orchestrator used by the worker."""
    from app.services.factory import build_campaign_orchestrator

    return build_campaign_orchestrator(include_verifier=True)


async def process_campaign_job(experiment_id: uuid.UUID, orchestrator=None) -> str:
    """Execute a single queued campaign job. Returns the terminal status."""
    orchestrator = orchestrator or build_orchestrator()
    logger.info(
        "Worker processing campaign job",
        extra={
            "event_name": "worker.campaign_started",
            "campaign_id": str(experiment_id),
        },
    )
    # Touch the liveness heartbeat up-front so a long job is never mistaken for
    # stale between dequeue and the orchestrator's first status write.
    await _touch_heartbeat(experiment_id)
    try:
        await orchestrator.run_attack_loop(experiment_id)
    except Exception as exc:
        logger.error(
            "Campaign worker failed",
            extra={
                "event_name": "worker.campaign_failed",
                "campaign_id": str(experiment_id),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        await _mark_failed(experiment_id)
        return "FAILED"

    status = await _get_status(experiment_id)
    logger.info(
        "Campaign worker finished",
        extra={
            "event_name": "worker.campaign_finished",
            "campaign_id": str(experiment_id),
            "status": status,
        },
    )
    return status or "FAILED"


async def _touch_heartbeat(experiment_id: uuid.UUID) -> None:
    """Record worker liveness on the experiment, if it still exists."""
    async with AsyncSessionLocal() as session:
        experiment = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        if experiment is not None:
            experiment.heartbeat_at = datetime.now(timezone.utc)
            await session.commit()


async def _mark_failed(experiment_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        experiment = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        if experiment and experiment.status not in ("COMPLETED", "FAILED"):
            experiment.status = "FAILED"
            experiment.finished_at = datetime.now(timezone.utc)
            await session.commit()


async def _get_status(experiment_id: uuid.UUID) -> Optional[str]:
    async with AsyncSessionLocal() as session:
        experiment = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        return experiment.status if experiment else None


async def _resume_stale_running(
    max_age_seconds: int, now: datetime
) -> list[uuid.UUID]:
    """Revert RUNNING campaigns stuck past the timeout to PENDING so the worker
    re-runs them and the orchestrator resumes from its DB round state.

    Staleness is judged on the worker liveness heartbeat (E-25): a campaign that
    has legitimately been running for hours bumps its heartbeat each round and is
    NEVER swept. Legacy rows without a heartbeat fall back to ``created_at``.
    """
    cutoff = now - timedelta(seconds=max_age_seconds)
    resumed: list[uuid.UUID] = []
    async with AsyncSessionLocal() as session:
        running = (
            await session.execute(
                select(Experiment).where(Experiment.status == "RUNNING")
            )
        ).scalars().all()
        stale = [
            experiment
            for experiment in running
            if _aware(experiment.heartbeat_at or experiment.created_at) < cutoff
        ]
        for experiment in stale:
            experiment.status = "PENDING"
            experiment.finished_at = None
            resumed.append(experiment.id)
        if stale:
            await session.commit()
    return resumed


async def recover_stale_campaigns(
    max_age_seconds: Optional[int] = None, now: Optional[datetime] = None
) -> dict:
    """
    Recovery routine run on worker startup:
      1. RUNNING campaigns whose liveness heartbeat (or, for legacy rows,
         ``created_at``) is older than the campaign timeout -> PENDING (resumed,
         the orchestrator continues from persisted round state on re-run).
      2. PENDING campaigns past the re-enqueue grace period -> re-queued.
      3. PENDING campaigns older than the campaign timeout -> FAILED.
    """
    max_age_seconds = max_age_seconds or settings.CAMPAIGN_TIMEOUT_SECONDS
    now = now or datetime.now(timezone.utc)
    pid_cutoff = now - timedelta(seconds=max_age_seconds)
    grace_cutoff = now - timedelta(seconds=PENDING_GRACE_SECONDS)

    resumed_running = await _resume_stale_running(max_age_seconds, now)

    re_enqueued: list[uuid.UUID] = []
    failed_pending: list[uuid.UUID] = []

    async with AsyncSessionLocal() as session:
        pending_stmt = select(Experiment).where(
            Experiment.status == "PENDING",
            Experiment.created_at < grace_cutoff,
        )
        if resumed_running:
            # Just-resumed campaigns must be re-queued, not swept into the
            # "very old PENDING -> FAILED" branch on the same pass.
            pending_stmt = pending_stmt.where(
                ~Experiment.id.in_(resumed_running)
            )
        pending = (
            await session.execute(pending_stmt)
        ).scalars().all()

        for experiment in pending:
            if _aware(experiment.created_at) < pid_cutoff:
                experiment.status = "FAILED"
                experiment.finished_at = now
                failed_pending.append(experiment.id)
        if failed_pending:
            await session.commit()

    for experiment_id in resumed_running + [
        e.id for e in pending if e.id not in failed_pending
    ]:
        if await enqueue_campaign(experiment_id):
            re_enqueued.append(experiment_id)

    if resumed_running or failed_pending or re_enqueued:
        logger.info(
            "Recovered stale campaigns",
            extra={
                "event_name": "worker.recovery",
                "num_resumed_running": len(resumed_running),
                "num_failed_pending": len(failed_pending),
                "num_requeued": len(re_enqueued),
            },
        )

    return {
        "resumed_running": resumed_running,
        "failed_pending": failed_pending,
        "requeued": re_enqueued,
    }


async def worker_loop() -> None:
    """Main worker loop: recover, then pull jobs from the queue forever."""
    logger.info(
        "Campaign worker started",
        extra={"event_name": "worker.started", "queue": "oracle:campaign:jobs"},
    )
    await recover_stale_campaigns()

    while True:
        job = await dequeue_campaign(timeout=5)
        if job is None:
            continue
        await process_campaign_job(job)


def main() -> None:
    """Entrypoint for ``python -m app.worker``."""
    try:
        asyncio.run(worker_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Campaign worker stopped", extra={"event_name": "worker.stopped"})


if __name__ == "__main__":
    main()
