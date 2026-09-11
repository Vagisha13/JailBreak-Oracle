"""PostgreSQL-backed campaign worker.

Long-running campaign execution is handled by this persistent worker process
instead of FastAPI ``BackgroundTasks``. The worker claims jobs from the
durable ``campaign_jobs`` PostgreSQL queue (``app.services.queue``), executes
them, and updates the persisted campaign state:

    PENDING -> RUNNING -> COMPLETED
    PENDING -> RUNNING -> FAILED (after bounded retries)

Run with: ``python -m app.worker`` (or the ``worker`` service in Docker).

Guarantees:
  * A worker crash cannot lose a job: the job row stays RUNNING and is
    re-admitted to the queue by stale recovery on the next worker startup,
    resuming a campaign from its persisted DB round state.
  * Failed jobs retry with backoff up to ``CAMPAIGN_JOB_MAX_ATTEMPTS``; the
    last failure reason is persisted on the job and the experiment.
  * Two workers never execute the same job (atomic claim) and never execute
    the same campaign concurrently (the existing PostgreSQL experiment lease).
  * The queue is scheduling state only; experiment ownership stays with
    ``app.services.lease``.
"""
import asyncio
import signal
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update as sa_update
from sqlalchemy.future import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import AgentRun, CampaignJob, Experiment
from app.services.queue import (
    JOB_COMPLETED,
    RETRYABLE,
    claim_next_job,
    enqueue_campaign,
    fail_job,
    mark_job_completed,
    recover_stale_jobs,
)

logger = get_logger("worker")

PENDING_GRACE_SECONDS = 300
JOB_POLL_INTERVAL_SECONDS = 2


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
    """Execute a single campaign. Returns the terminal execution status.

    Kept queue-agnostic so the lease/execution tests exercise it directly.
    Queue bookkeeping (claim/retry/complete) lives in :func:`execute_job`.
    """
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
        summary = await orchestrator.run_attack_loop(experiment_id)
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

    if summary is None or summary.status == "SKIPPED":
        # The campaign was claimed/executed by another worker (or is already in
        # a terminal state). Safely exit instead of running concurrently.
        logger.info(
            "Campaign job skipped (another worker owns the campaign)",
            extra={
                "event_name": "worker.campaign_skipped",
                "campaign_id": str(experiment_id),
                "status": summary.status if summary else "MISSING",
            },
        )
        return "SKIPPED"

    logger.info(
        "Campaign worker finished",
        extra={
            "event_name": "worker.campaign_finished",
            "campaign_id": str(experiment_id),
            "status": summary.status,
        },
    )
    return summary.status


async def execute_job(job: CampaignJob, orchestrator=None) -> str:
    """Claim-owned wrapper: run the campaign, then settle the job row.

    COMPLETED -> the job is done. SKIPPED -> another owner already finished the
    campaign; the job is closed out (nothing to re-run). FAILED -> the job is
    retired or retried; retries reset the experiment to PENDING so the next
    attempt can re-acquire the campaign lease.
    """
    status = await process_campaign_job(job.experiment_id, orchestrator)

    if status in ("COMPLETED", "SKIPPED"):
        note = "skipped (campaign already owned/terminal)" if status == "SKIPPED" else None
        await mark_job_completed(job.id, status=JOB_COMPLETED, note=note)
        return status

    outcome = await fail_job(
        job.id,
        error=await _last_campaign_failure(job.experiment_id)
        or f"campaign {job.experiment_id} execution failed",
    )
    if outcome == RETRYABLE:
        await _reset_experiment_for_retry(job.experiment_id)
    return status


async def _last_campaign_failure(experiment_id: uuid.UUID) -> Optional[str]:
    """Fetch the campaign's persisted failure reason (same source the status
    endpoint reads) so the job's ``last_error`` is queryable and truthful."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(AgentRun.state_json)
            .where(
                AgentRun.experiment_id == experiment_id,
                AgentRun.agent_type == "campaign",
            )
            .order_by(AgentRun.created_at.desc())
        )
        for (state,) in (await session.execute(stmt)).all():
            if isinstance(state, dict) and state.get("status") == "FAILED":
                reason = state.get("reason") or "execution_error"
                error_type = state.get("error_type")
                message = state.get("error_message")
                detail = f" ({error_type}): {message}" if error_type else ""
                return f"{reason}{detail}"
    return None


async def _reset_experiment_for_retry(experiment_id: uuid.UUID) -> None:
    """Return a FAILED campaign to PENDING so a retried job can re-acquire the
    lease. Mirrors stale-recovery: clears the claim token + expiry."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_update(Experiment)
            .where(Experiment.id == experiment_id)
            .values(
                status="PENDING",
                finished_at=None,
                heartbeat_at=datetime.now(timezone.utc),
                claim_owner=None,
                lease_expires_at=None,
            )
        )
        await session.commit()


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
            # A failed run can no longer hold the multi-worker lease.
            experiment.claim_owner = None
            experiment.lease_expires_at = None
            await session.commit()


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
            # Hand the multi-worker lease back so the resumed run can reclaim.
            experiment.claim_owner = None
            experiment.lease_expires_at = None
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


async def worker_loop(stop: Optional[asyncio.Event] = None) -> None:
    """Recover stale state, then claim and run durable queue jobs forever.

    ``stop`` enables graceful shutdown: once set, the worker finishes any
    in-flight job and stops claiming new ones (no abandoned mid-execution work).
    """
    logger.info(
        "Campaign worker started",
        extra={"event_name": "worker.started", "queue": "postgresql:campaign_jobs"},
    )
    await recover_stale_campaigns()
    await recover_stale_jobs()

    while stop is None or not stop.is_set():
        job = await claim_next_job()
        if job is None:
            await asyncio.sleep(JOB_POLL_INTERVAL_SECONDS)
            continue
        try:
            await execute_job(job)
        except Exception as exc:
            # A crash inside execution must never kill the worker process: the
            # job is left RUNNING and stale-recovery re-admits it on restart.
            logger.error(
                "Unhandled error while executing campaign job",
                extra={
                    "event_name": "worker.job_unhandled",
                    "job_id": str(job.id),
                    "campaign_id": str(job.experiment_id),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )

    logger.info("Campaign worker stopped", extra={"event_name": "worker.stopped"})


def main() -> None:
    """Entrypoint for ``python -m app.worker``."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    handled = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
            handled = True
        except (NotImplementedError, RuntimeError):
            # Windows / restricted environments: rely on KeyboardInterrupt.
            continue
    try:
        loop.run_until_complete(worker_loop(stop=stop if handled else None))
    except KeyboardInterrupt:
        logger.info("Campaign worker interrupted", extra={"event_name": "worker.interrupted"})
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:  # pragma: no cover - loop teardown is best-effort
            pass
        loop.close()


if __name__ == "__main__":
    main()
