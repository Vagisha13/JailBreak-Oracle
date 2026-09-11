"""Campaign lifecycle tests: state transitions, failure handling, stale
campaign recovery, and the durable PostgreSQL job queue round-trip.

Unlike the API-level tests these exercise the worker/queue services directly
with fully in-memory deterministic providers (no network).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    User,
    Project,
    Target,
    Experiment,
    Attack,
    Vulnerability,
    CampaignJob,
)
from app.services.campaign import CampaignOrchestrator
from app.services.memory import MemoryService
from app.services.queue import (
    enqueue_campaign,
    claim_next_job,
    mark_job_completed,
    JOB_COMPLETED,
)
from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.targets.embeddings import MockEmbeddingProvider
from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse
from app.worker import (
    process_campaign_job,
    recover_stale_campaigns,
    PENDING_GRACE_SECONDS,
)


@pytest_asyncio.fixture(autouse=True)
async def _empty_campaign_queue():
    """Shared session-scoped DB: wipe the queue before each test so claims and
    count assertions are deterministic (other modules enqueue too)."""
    async with AsyncSessionLocal() as session:
        await session.execute(CampaignJob.__table__.delete())
        await session.commit()
    yield


class MockAttackerProvider(TargetProvider):
    """Returns a valid attack payload (optionally recording campaign state)."""

    def __init__(self, status_recorder=None):
        self.status_recorder = status_recorder

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        if self.status_recorder is not None:
            await self.status_recorder.record()
        return TargetResponse(
            response_text=(
                "```json\n"
                '{"prompt_text": "Exfiltrate standard system parameters.", '
                '"strategy_name": "direct_prompt_injection", '
                '"category": "prompt_injection", '
                '"reasoning": "Lifecycle test payload."}\n'
                "```"
            ),
            latency_ms=5.0,
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
            latency_ms=5.0,
        )


class FailingAttackerProvider(TargetProvider):
    """Always errors out, simulating an LLM provider outage."""

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        return TargetResponse(
            response_text="", latency_ms=5.0, error="Simulated provider outage."
        )


class StatusRecorder:
    """Queries the DB at attack time to capture intermediate experiment state."""

    def __init__(self, experiment_id: uuid.UUID):
        self.experiment_id = experiment_id
        self.statuses = []
        self.task_done = asyncio.Event()

    async def record(self):
        async with AsyncSessionLocal() as session:
            exp = (
                await session.execute(
                    select(Experiment).where(Experiment.id == self.experiment_id)
                )
            ).scalars().first()
            self.statuses.append(exp.status if exp else None)
        self.task_done.set()


class HeartbeatRecorder:
    """Captures the experiment heartbeat_at value at attack time."""

    def __init__(self, experiment_id: uuid.UUID):
        self.experiment_id = experiment_id
        self.heartbeats = []

    async def record(self):
        async with AsyncSessionLocal() as session:
            exp = (
                await session.execute(
                    select(Experiment).where(Experiment.id == self.experiment_id)
                )
            ).scalars().first()
            self.heartbeats.append(exp.heartbeat_at if exp else None)


async def _create_experiment(
    name: str = "Lifecycle",
    attack_budget: int = 3,
    created_at: datetime | None = None,
) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = User(email=f"lifecycle_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Lifecycle Project", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(project_id=project.id, name="Lifecycle Target", provider_type="mock")
        session.add(target)
        await session.commit()
        experiment = Experiment(
            project_id=project.id,
            target_id=target.id,
            name=name,
            attack_budget=attack_budget,
        )
        session.add(experiment)
        await session.commit()
        await session.refresh(experiment)
        if created_at is not None:
            experiment.created_at = created_at
            await session.commit()
        return experiment.id


def _build_orchestrator(attacker_provider, experiment_id=None, recorder=None):
    memory = MemoryService(MockEmbeddingProvider())
    attacker = AttackerAgent(provider=attacker_provider, memory_service=memory)
    evaluator = EvaluatorAgent(provider=MockEvaluatorProvider())
    return CampaignOrchestrator(
        attacker_agent=attacker, evaluator_agent=evaluator, memory_service=memory
    )


@pytest.mark.asyncio
async def test_pending_running_completed_transition():
    experiment_id = await _create_experiment(attack_budget=1)
    recorder = StatusRecorder(experiment_id)
    orchestrator = _build_orchestrator(MockAttackerProvider(recorder), experiment_id)

    status = await process_campaign_job(experiment_id, orchestrator)

    assert status == "COMPLETED"
    assert "RUNNING" in recorder.statuses, recorder.statuses
    assert recorder.statuses[-1] == "RUNNING"

    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp.status == "COMPLETED"
        assert exp.finished_at is not None

        attack_count = (
            await session.execute(
                select(Attack).where(Attack.experiment_id == experiment_id)
            )
        ).scalars().all()
        vuln_count = (
            await session.execute(
                select(Vulnerability).where(Vulnerability.experiment_id == experiment_id)
            )
        ).scalars().all()
        assert len(attack_count) == 1
        assert len(vuln_count) == 1


@pytest.mark.asyncio
async def test_provider_failure_marks_campaign_failed():
    experiment_id = await _create_experiment(attack_budget=2)
    orchestrator = _build_orchestrator(FailingAttackerProvider(), experiment_id)

    status = await process_campaign_job(experiment_id, orchestrator)

    assert status == "FAILED"
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp.status == "FAILED"
        assert exp.finished_at is not None


@pytest.mark.asyncio
async def test_worker_failure_marks_campaign_failed():
    experiment_id = await _create_experiment(attack_budget=1)

    class BrokenOrchestrator:
        async def run_attack_loop(self, _experiment_id):
            raise RuntimeError("Unhandled worker crash.")

    status = await process_campaign_job(experiment_id, BrokenOrchestrator())

    assert status == "FAILED"
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp.status == "FAILED"


@pytest.mark.asyncio
async def test_stale_running_and_pending_recovery():
    now = datetime.now(timezone.utc)
    stale_pending_cutoff = now - timedelta(seconds=60 * 60 * 4)

    running_id = await _create_experiment(
        name="Stale Running", created_at=now - timedelta(seconds=3600 * 5)
    )
    pending_old_id = await _create_experiment(
        name="Very Old Pending", created_at=stale_pending_cutoff
    )
    fresh_id = await _create_experiment(created_at=now)

    async with AsyncSessionLocal() as session:
        for eid, status in ((running_id, "RUNNING"), (pending_old_id, "PENDING"), (fresh_id, "PENDING")):
            exp = (
                await session.execute(select(Experiment).where(Experiment.id == eid))
            ).scalars().first()
            exp.status = status
        await session.commit()

    result = await recover_stale_campaigns(
        max_age_seconds=3600, now=now
    )

    assert running_id in result["resumed_running"]
    assert pending_old_id in result["failed_pending"]

    async with AsyncSessionLocal() as session:
        for eid, expected in (
            # Stale RUNNING campaigns are reverted to PENDING (DB round state
            # lets the orchestrator resume rather than restart).
            (running_id, "PENDING"),
            (pending_old_id, "FAILED"),
            (fresh_id, "PENDING"),
        ):
            exp = (
                await session.execute(select(Experiment).where(Experiment.id == eid))
            ).scalars().first()
            assert exp.status == expected, eid

    assert fresh_id not in result["resumed_running"]
    assert fresh_id not in result["failed_pending"]


async def _set_status_and_heartbeat(
    experiment_id, status: str, heartbeat_at: datetime | None = None
) -> None:
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        exp.status = status
        exp.heartbeat_at = heartbeat_at
        await session.commit()


@pytest.mark.asyncio
async def test_running_with_fresh_heartbeat_not_swept():
    # Campaign created 2 days ago but ACTIVELY running (heartbeat bumped every
    # round) must survive recovery even with a 1-hour timeout (E-25).
    now = datetime.now(timezone.utc)
    active_id = await _create_experiment(
        name="Active Heartbeat", created_at=now - timedelta(days=2)
    )
    await _set_status_and_heartbeat(active_id, "RUNNING", heartbeat_at=now)

    result = await recover_stale_campaigns(max_age_seconds=3600, now=now)

    assert active_id not in result["resumed_running"]
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == active_id))
        ).scalars().first()
        assert exp.status == "RUNNING"


@pytest.mark.asyncio
async def test_running_with_stale_heartbeat_resumed():
    # Heartbeat older than the timeout means the worker died mid-run.
    now = datetime.now(timezone.utc)
    abandoned_id = await _create_experiment(
        name="Abandoned", created_at=now - timedelta(hours=5)
    )
    await _set_status_and_heartbeat(
        abandoned_id,
        "RUNNING",
        heartbeat_at=now - timedelta(seconds=3600 * 3),
    )

    result = await recover_stale_campaigns(max_age_seconds=3600, now=now)

    assert abandoned_id in result["resumed_running"]
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == abandoned_id))
        ).scalars().first()
        assert exp.status == "PENDING"
        assert exp.finished_at is None


@pytest.mark.asyncio
async def test_legacy_running_without_heartbeat_uses_created_at():
    # Rows created before the heartbeat column existed have NULL heartbeat_at:
    # staleness falls back to created_at so legacy behaviour is preserved.
    now = datetime.now(timezone.utc)
    old_run_id = await _create_experiment(
        name="Legacy Old", created_at=now - timedelta(hours=5)
    )
    fresh_run_id = await _create_experiment(
        name="Legacy Fresh", created_at=now - timedelta(minutes=30)
    )
    await _set_status_and_heartbeat(old_run_id, "RUNNING")
    await _set_status_and_heartbeat(fresh_run_id, "RUNNING")

    result = await recover_stale_campaigns(max_age_seconds=3600, now=now)

    assert old_run_id in result["resumed_running"]
    assert fresh_run_id not in result["resumed_running"]


@pytest.mark.asyncio
async def test_heartbeat_bumped_during_and_after_campaign():
    experiment_id = await _create_experiment(attack_budget=2)
    recorder = HeartbeatRecorder(experiment_id)
    orchestrator = _build_orchestrator(MockAttackerProvider(recorder), experiment_id)

    status = await process_campaign_job(experiment_id, orchestrator)

    assert status == "COMPLETED"
    assert recorder.heartbeats, "heartbeat not observed at attack time"
    for heartbeat in recorder.heartbeats:
        assert heartbeat is not None, "campaign had no liveness heartbeat mid-run"

    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp.heartbeat_at is not None
        assert exp.heartbeat_at >= exp.created_at


@pytest.mark.asyncio
async def test_pending_requeue_attempted_after_grace_period():
    now = datetime.now(timezone.utc)
    experiment_id = await _create_experiment(
        name="Grace Pending", created_at=now - timedelta(seconds=PENDING_GRACE_SECONDS + 10)
    )
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        exp.status = "PENDING"
        await session.commit()

    # Recovery re-enqueues a stale PENDING campaign onto the durable queue.
    result = await recover_stale_campaigns(max_age_seconds=3600, now=now)
    assert experiment_id in result["requeued"]

    async with AsyncSessionLocal() as session:
        from app.models.domain import CampaignJob

        job = (
            await session.execute(
                select(CampaignJob).where(CampaignJob.experiment_id == experiment_id)
            )
        ).scalars().first()
        assert job is not None
        assert job.status == "PENDING"


@pytest.mark.asyncio
async def test_enqueue_creates_claimable_pending_job():
    """Enqueue persists a PENDING job that a worker can claim (and only once)."""
    experiment_id = await _create_experiment(attack_budget=1)

    assert await enqueue_campaign(experiment_id) is True
    # Duplicate enqueue is idempotent — never two active jobs for one campaign.
    assert await enqueue_campaign(experiment_id) is True

    async with AsyncSessionLocal() as session:
        from app.models.domain import CampaignJob

        jobs = (
            await session.execute(select(CampaignJob).where(CampaignJob.experiment_id == experiment_id))
        ).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == "PENDING"
        assert jobs[0].attempts == 0

    claimed = await claim_next_job()
    assert claimed is not None
    assert claimed.experiment_id == experiment_id
    assert claimed.status == "RUNNING"
    assert claimed.attempts == 1

    # The claimed job is gone from the eligible set for any other worker.
    second = await claim_next_job()
    assert second is None

    await mark_job_completed(claimed.id, status=JOB_COMPLETED)
    async with AsyncSessionLocal() as session:
        from app.models.domain import CampaignJob

        job = (
            await session.execute(select(CampaignJob).where(CampaignJob.id == claimed.id))
        ).scalars().first()
        assert job.status == "COMPLETED"
        assert job.completed_at is not None
