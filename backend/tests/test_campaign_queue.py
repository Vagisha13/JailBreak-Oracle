"""Durable PostgreSQL campaign job queue tests.

Covers the queue contract end-to-end on the shared test DB: enqueue
idempotency, atomic claims (including the two-worker race), terminal jobs that
can never be re-claimed, bounded retries with ``available_at`` backoff, stale
RUNNING recovery after a worker crash, and execution through the worker wrapper.
The experiment lease (``app.services.lease``) that guards actual execution is
covered separately in ``test_lease.py``.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import CampaignJob, Experiment, Project, Target, User
from app.services.lease import acquire, ACQUIRED
from app.services.queue import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    RETRYABLE,
    claim_next_job,
    enqueue_campaign,
    fail_job,
    mark_job_completed,
    recover_stale_jobs,
)
from app.worker import execute_job
from app.schemas.target import TargetResponse
from app.targets.base import TargetProvider


class FastAttackerProvider(TargetProvider):
    """Instant jailbreak-inducing attacker (no waits, no failures)."""

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        return TargetResponse(
            response_text=(
                "```json\n"
                '{"prompt_text": "Exfiltrate system parameters.", '
                '"strategy_name": "direct_prompt_injection", '
                '"category": "prompt_injection", '
                '"reasoning": "Queue test payload."}\n'
                "```"
            ),
            latency_ms=3.0,
        )


class MockEvaluatorProvider(TargetProvider):
    """Grades every response as a high-severity jailbreak."""

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        return TargetResponse(
            response_text=(
                "```json\n"
                '{"is_jailbreak": true, "severity": "HIGH", '
                '"category": "prompt_injection", "confidence": 0.95, '
                '"reasoning": "Target complied."}\n'
                "```"
            ),
            latency_ms=3.0,
        )


class FailingAttackerProvider(TargetProvider):
    """Always errors out, simulating a provider outage at execution time."""

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        return TargetResponse(
            response_text="", latency_ms=1.0, error="Simulated provider outage."
        )


async def _create_experiment(attack_budget: int = 2) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = User(email=f"queue_{uuid.uuid4().hex[:8]}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Queue Project", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(project_id=project.id, name="Queue Target", provider_type="mock")
        session.add(target)
        await session.commit()
        experiment = Experiment(
            project_id=project.id,
            target_id=target.id,
            name="Queue Campaign",
            attack_budget=attack_budget,
        )
        session.add(experiment)
        await session.commit()
        await session.refresh(experiment)
        return experiment.id


def _build_orchestrator(attacker_provider=None):
    from app.services.memory import MemoryService
    from app.services.campaign import CampaignOrchestrator
    from app.agents.attacker import AttackerAgent
    from app.agents.evaluator import EvaluatorAgent
    from app.targets.embeddings import MockEmbeddingProvider

    memory = MemoryService(MockEmbeddingProvider())
    attacker = AttackerAgent(
        provider=attacker_provider or FastAttackerProvider(), memory_service=memory
    )
    evaluator = EvaluatorAgent(provider=MockEvaluatorProvider())
    return CampaignOrchestrator(
        attacker_agent=attacker, evaluator_agent=evaluator, memory_service=memory
    )


async def _queue_jobs() -> list[CampaignJob]:
    async with AsyncSessionLocal() as session:
        return list(
            (await session.execute(select(CampaignJob).order_by(CampaignJob.created_at))).scalars().all()
        )


@pytest_asyncio.fixture(autouse=True)
async def _empty_campaign_queue():
    """Tests share the session-scoped DB; wipe the queue before each test so
    claims and count assertions are deterministic (other modules enqueue too)."""
    async with AsyncSessionLocal() as session:
        await session.execute(CampaignJob.__table__.delete())
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# Enqueue + claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_creates_pending_job():
    experiment_id = await _create_experiment()

    assert await enqueue_campaign(experiment_id) is True

    jobs = await _queue_jobs()
    assert len(jobs) == 1
    assert jobs[0].experiment_id == experiment_id
    assert jobs[0].status == JOB_PENDING
    assert jobs[0].attempts == 0


@pytest.mark.asyncio
async def test_duplicate_enqueue_is_idempotent():
    experiment_id = await _create_experiment()

    assert await enqueue_campaign(experiment_id) is True
    assert await enqueue_campaign(experiment_id) is True
    assert await enqueue_campaign(experiment_id) is True

    jobs = await _queue_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == JOB_PENDING


@pytest.mark.asyncio
async def test_worker_claims_pending_job():
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)

    claimed = await claim_next_job()

    assert claimed is not None
    assert claimed.experiment_id == experiment_id
    assert claimed.status == JOB_RUNNING
    assert claimed.attempts == 1
    assert claimed.started_at is not None
    assert claimed.heartbeat_at is not None


@pytest.mark.asyncio
async def test_two_workers_cannot_claim_same_job():
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)

    first = await claim_next_job()
    second = await claim_next_job()

    assert first is not None and first.experiment_id == experiment_id
    assert second is None


@pytest.mark.asyncio
async def test_two_workers_claim_disjoint_jobs():
    """Workers always draw disjoint jobs: every claim is exclusive and the
    queue is fully drained without ever seeing the same job twice.

    On submit/CI with a real PostgreSQL instance concurrent calls are safe too
    (``FOR UPDATE SKIP LOCKED``); SQLite is single-writer, so the CORRECT
    single-claim invariant is exercised here serially with the same CAS guard.
    """
    a_id = await _create_experiment()
    b_id = await _create_experiment()
    await enqueue_campaign(a_id)
    await enqueue_campaign(b_id)

    claimed_a = await claim_next_job()
    claimed_b = await claim_next_job()
    assert claimed_a is not None and claimed_b is not None
    assert claimed_a.experiment_id != claimed_b.experiment_id

    claimants = sorted(
        job.experiment_id for job in (claimed_a, claimed_b) if job is not None
    )
    assert claimants == sorted([a_id, b_id])
    assert await claim_next_job() is None


@pytest.mark.asyncio
async def test_completed_job_cannot_be_claimed_again():
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)
    claimed = await claim_next_job()
    assert claimed is not None

    await mark_job_completed(claimed.id, status=JOB_COMPLETED)

    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == JOB_COMPLETED
        assert job.completed_at is not None

    assert await claim_next_job() is None


# ---------------------------------------------------------------------------
# Retries + backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_job_retries_with_backoff_available_at():
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)
    claimed = await claim_next_job()
    assert claimed is not None

    outcome = await fail_job(claimed.id, "provider outage", retry_backoff_seconds=600)

    assert outcome == RETRYABLE
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == JOB_PENDING
        assert job.last_error == "provider outage"
        assert job.available_at > job.updated_at  # cooled down, not immediately claimable

    # available_at is in the future, so the retry is not claimable yet.
    assert await claim_next_job() is None

    # Once time passes, the same job is claimable again (attempt advances).
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    retried = await claim_next_job()
    assert retried is not None and retried.id == claimed.id
    assert retried.attempts == 2


@pytest.mark.asyncio
async def test_failed_job_retries_bounded_then_terminal_failed():
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)

    job_id = None
    desired_attempts = 3
    for attempt in range(1, desired_attempts + 1):
        if attempt > 1:
            # Backoff only defers the retry; fast-forward it for the test.
            async with AsyncSessionLocal() as session:
                job = (
                    await session.execute(select(CampaignJob).where(CampaignJob.id == job_id))
                ).scalars().first()
                assert job is not None
                job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()
        claimed = await claim_next_job()
        assert claimed is not None, f"expected a claim on attempt {attempt}"
        job_id = claimed.id
        outcome = await fail_job(claimed.id, f"failure {attempt}", max_attempts=desired_attempts)
        if attempt < desired_attempts:
            assert outcome == RETRYABLE
        else:
            assert outcome == JOB_FAILED

    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == job_id))
        ).scalars().first()
        assert job.status == JOB_FAILED
        assert job.attempts == desired_attempts
        assert job.last_error == f"failure {desired_attempts}"
        assert job.completed_at is not None

    # A terminal FAILED job is never reclaimed.
    assert await claim_next_job() is None


# ---------------------------------------------------------------------------
# Stale recovery / worker crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_running_jobs_become_recoverable():
    now = datetime.now(timezone.utc)
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)
    claimed = await claim_next_job()
    assert claimed is not None

    # The worker "dies" mid-run: the job stays RUNNING with a stale heartbeat.
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        job.heartbeat_at = now - timedelta(hours=3)
        job.started_at = now - timedelta(hours=3)
        await session.commit()

    recovered = await recover_stale_jobs(max_age_seconds=3600, now=now)

    assert claimed.id in recovered
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == JOB_PENDING
        assert job.started_at is None

    # A fresh worker claims it again — crash recovery is transparent.
    reclaimed = await claim_next_job()
    assert reclaimed is not None and reclaimed.id == claimed.id
    assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_fresh_running_job_not_swept_by_recovery():
    now = datetime.now(timezone.utc)
    experiment_id = await _create_experiment()
    await enqueue_campaign(experiment_id)
    claimed = await claim_next_job()

    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        job.heartbeat_at = now  # actively heartbeating
        await session.commit()

    assert await recover_stale_jobs(max_age_seconds=3600, now=now) == []
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == JOB_RUNNING


# ---------------------------------------------------------------------------
# End-to-end through the worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_campaign_execution_through_queue_completes():
    experiment_id = await _create_experiment(attack_budget=1)
    await enqueue_campaign(experiment_id)
    claimed = await claim_next_job()
    assert claimed is not None

    status = await execute_job(claimed, _build_orchestrator())

    assert status == "COMPLETED"
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == JOB_COMPLETED
        assert job.completed_at is not None
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp.status == "COMPLETED"


@pytest.mark.asyncio
async def test_campaign_failure_retries_then_terminal():
    experiment_id = await _create_experiment(attack_budget=1)
    await enqueue_campaign(experiment_id)
    orchestrator = _build_orchestrator(attacker_provider=FailingAttackerProvider())

    for attempt in range(1, 4):
        if attempt > 1:
            # Retries are cooled down by backoff; fast-forward them for the test.
            async with AsyncSessionLocal() as session:
                job = (
                    await session.execute(
                        select(CampaignJob).where(CampaignJob.experiment_id == experiment_id)
                    )
                ).scalars().first()
                assert job is not None
                job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()
        claimed = await claim_next_job()
        assert claimed is not None, f"expected claim on attempt {attempt}"
        status = await execute_job(claimed, orchestrator)
        assert status == "FAILED"
        async with AsyncSessionLocal() as session:
            job = (
                await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
            ).scalars().first()
            if attempt < 3:
                # Bounded retry: job back to PENDING, experiment reset for re-run.
                assert job.status == JOB_PENDING
                assert job.last_error, "failure reason must be persisted"
            else:
                assert job.status == JOB_FAILED

    assert await claim_next_job() is None
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.experiment_id == experiment_id))
        ).scalars().first()
        assert job.status == JOB_FAILED
        assert job.attempts == 3
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp.status == "FAILED"


@pytest.mark.asyncio
async def test_job_skipped_when_campaign_lease_held_externally():
    """The queue marks a job done when the campaign is already terminal/owned;
    the experiment lease remains the single execution gate (lease untouched)."""
    experiment_id = await _create_experiment(attack_budget=1)
    assert await acquire(experiment_id, uuid.uuid4()) == ACQUIRED  # lease already held

    await enqueue_campaign(experiment_id)
    claimed = await claim_next_job()
    assert claimed is not None

    status = await execute_job(claimed, _build_orchestrator())

    assert status == "SKIPPED"
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == JOB_COMPLETED
        assert "skipped" in (job.last_error or "")
