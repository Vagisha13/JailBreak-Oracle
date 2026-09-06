"""Campaign lifecycle tests: state transitions, failure handling, stale
campaign recovery, and the Redis job queue round-trip.

Unlike the API-level tests these exercise the worker/queue services directly
with fully in-memory deterministic providers (no network).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy.future import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.domain import User, Project, Target, Experiment, Attack, Vulnerability
from app.services.campaign import CampaignOrchestrator
from app.services.memory import MemoryService
from app.services.queue import enqueue_campaign, dequeue_campaign, JOB_QUEUE_KEY
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


class FakeRedis:
    """Minimal stand-in for the async Redis client used by the queue.

    Mirrors real Redis semantics: values are stored as bytes and ``blpop``
    returns ``(key, value_bytes)``.
    """

    def __init__(self):
        self.list = []
        self.counters = {}

    async def rpush(self, key: str, value: str) -> int:
        self.list.append((key, value.encode()))
        return len(self.list)

    async def blpop(self, key: str, timeout: int = 0):
        if not self.list:
            return None
        return self.list.pop(0)

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 60


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

    assert running_id in result["failed_running"]
    assert pending_old_id in result["failed_pending"]

    async with AsyncSessionLocal() as session:
        for eid, expected in (
            (running_id, "FAILED"),
            (pending_old_id, "FAILED"),
            (fresh_id, "PENDING"),
        ):
            exp = (
                await session.execute(select(Experiment).where(Experiment.id == eid))
            ).scalars().first()
            assert exp.status == expected, eid

    assert fresh_id not in result["failed_running"]
    assert fresh_id not in result["failed_pending"]


@pytest.mark.asyncio
async def test_pending_requeue_attempted_after_grace_period(monkeypatch):
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

    fake = FakeRedis()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake:6379")
    monkeypatch.setattr("app.services.queue._redis_client", fake)
    monkeypatch.setattr("app.services.queue._redis_attempted", True)

    result = await recover_stale_campaigns(max_age_seconds=3600, now=now)
    assert experiment_id in result["requeued"]


@pytest.mark.asyncio
async def test_queue_round_trip_with_fake_redis():
    fake = FakeRedis()
    exp_id = uuid.uuid4()

    assert await enqueue_campaign(exp_id, redis_client=fake) is True
    assert await enqueue_campaign(exp_id, redis_client=fake) is True

    assert await dequeue_campaign(redis_client=fake, timeout=0) == exp_id
    assert await dequeue_campaign(redis_client=fake, timeout=0) == exp_id
    assert await dequeue_campaign(redis_client=fake, timeout=0) is None


@pytest.mark.asyncio
async def test_invalid_job_payload_ignored():
    fake = FakeRedis()
    fake.list.append((JOB_QUEUE_KEY, b"not-a-uuid"))
    assert await dequeue_campaign(redis_client=fake, timeout=0) is None
