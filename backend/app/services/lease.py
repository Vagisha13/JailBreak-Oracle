"""Atomic DB-backed campaign lease (multi-worker claim).

Two workers dequeuing the same campaign job must not execute it concurrently.
The claim is a compare-and-swap on the ``experiments`` row:

    PENDING -> RUNNING   (sets ``claim_owner`` + ``lease_expires_at``)

The UPDATE is matched on ``id AND status='PENDING'``, so at most one worker's
statement can affect a row. The loser's UPDATE touches 0 rows and the worker
never reaches token-spending execution.

The lease is heartbeat-compatible by construction: the orchestrator renews the
expiry on the same per-round liveness heartbeat recovery keys on, so a
legitimately active campaign is never mistaken for abandoned; a crashed worker
leaves the expiry to age out and recovery reverts the row (clearing the token)
exactly like the existing RUNNING -> PENDING path.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update as sa_update
from sqlalchemy.future import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment

logger = get_logger("lease")

# Claim outcomes (returned by ``acquire``).
ACQUIRED = "ACQUIRED"        # this worker won the atomic PENDING -> RUNNING CAS
RENEWED = "RENEWED"          # already ours; lease extended (idempotent re-entry)
BUSY = "BUSY"                # RUNNING under another worker's lease
TERMINAL = "TERMINAL"        # COMPLETED/FAILED; never re-run
NOT_FOUND = "NOT_FOUND"      # experiment row does not exist


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_expiry(lease_seconds: Optional[int]) -> datetime:
    return _now() + timedelta(seconds=lease_seconds or settings.CAMPAIGN_TIMEOUT_SECONDS)


async def acquire(
    experiment_id: uuid.UUID,
    owner_token: uuid.UUID,
    lease_seconds: Optional[int] = None,
) -> str:
    """Atomically claim the campaign for ``owner_token``.

    Returns one of :data:`ACQUIRED`, :data:`RENEWED`, :data:`BUSY`,
    :data:`TERMINAL`, :data:`NOT_FOUND`. Re-acquiring with the *same* token
    (worker re-entry / retry) renews the lease instead of failing.
    """
    async with AsyncSessionLocal() as session:
        # Atomic CAS: only wins when the row is still PENDING. The liveness
        # heartbeat is set in the same write so a freshly-claimed campaign can
        # never be swept by stale-recovery before its first round heartbeat.
        now = _now()
        result = await session.execute(
            sa_update(Experiment)
            .where(
                Experiment.id == experiment_id,
                Experiment.status == "PENDING",
            )
            .values(
                status="RUNNING",
                claim_owner=owner_token,
                lease_expires_at=_lease_expiry(lease_seconds),
                heartbeat_at=now,
            )
        )
        await session.commit()
        if result.rowcount == 1:
            return ACQUIRED

        # Not PENDING: re-entry by the same owner renews; anything else is
        # already owned or terminal.
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        if exp is None:
            return NOT_FOUND
        if exp.status in ("COMPLETED", "FAILED"):
            return TERMINAL
        if exp.claim_owner == owner_token:
            await renew(experiment_id, owner_token, lease_seconds)
            return RENEWED
        return BUSY


async def renew(
    experiment_id: uuid.UUID,
    owner_token: uuid.UUID,
    lease_seconds: Optional[int] = None,
) -> bool:
    """Extend the lease, but only when this worker actually owns it. A stale
    worker cannot extend a lease that recovery already handed to someone else."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa_update(Experiment)
            .where(
                Experiment.id == experiment_id,
                Experiment.claim_owner == owner_token,
            )
            .values(lease_expires_at=_lease_expiry(lease_seconds))
        )
        await session.commit()
        return result.rowcount == 1


async def release(
    experiment_id: uuid.UUID,
    owner_token: uuid.UUID,
) -> None:
    """Best-effort unconditional release when this worker owns the lease.
    Terminal status is set by the orchestrator; this only clears the token."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_update(Experiment)
            .where(
                Experiment.id == experiment_id,
                Experiment.claim_owner == owner_token,
            )
            .values(claim_owner=None, lease_expires_at=None)
        )
        await session.commit()


async def owns_lease(experiment_id: uuid.UUID, owner_token: uuid.UUID) -> bool:
    """Proof-of-ownership used at execution boundaries."""
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        return bool(exp and exp.claim_owner == owner_token)


async def clear_lease(experiment_id: uuid.UUID) -> None:
    """Unconditional lease clear used by stale-campaign recovery so a re-claimed
    row starts fresh (no residual token from a dead worker)."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_update(Experiment)
            .where(Experiment.id == experiment_id)
            .values(claim_owner=None, lease_expires_at=None)
        )
        await session.commit()


__all__ = [
    "ACQUIRED",
    "RENEWED",
    "BUSY",
    "TERMINAL",
    "NOT_FOUND",
    "acquire",
    "renew",
    "release",
    "owns_lease",
    "clear_lease",
]
