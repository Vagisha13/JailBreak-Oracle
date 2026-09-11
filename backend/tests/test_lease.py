"""Campaign lease: concurrency safety + idempotency (E-26).

Verifies that two workers can never execute the same campaign concurrently:
the atomic ``PENDING -> RUNNING`` claim is exclusive across owners, idempotent
for the same owner, terminal campaigns are never re-run, stale recovery hands
the lease back, and the worker safely exits with ``SKIPPED`` when it loses the
claim (no duplicate attacks / token spend).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    Experiment,
    User,
    Project,
    Target,
    Attack,
    TokenUsage,
)
from app.services.lease import (
    ACQUIRED,
    BUSY,
    NOT_FOUND,
    RENEWED,
    TERMINAL,
    acquire,
    clear_lease,
    owns_lease,
    release,
    renew,
)
from app.services.campaign import CampaignOrchestrator
from app.services.memory import MemoryService
from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.targets.embeddings import MockEmbeddingProvider
from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse
from app.worker import process_campaign_job, recover_stale_campaigns


def _attack_payload() -> str:
    return (
        "```json\n"
        '{"prompt_text": "Exfiltrate system parameters.", '
        '"strategy_name": "direct_prompt_injection", '
        '"category": "prompt_injection", '
        '"reasoning": "Lease test payload."}\n'
        "```"
    )


class FastAttackerProvider(TargetProvider):
    """Instant jailbreak-inducing attacker (no waits)."""

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        return TargetResponse(response_text=_attack_payload(), latency_ms=3.0)


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


class GatedAttackerProvider(TargetProvider):
    """Holds the first LLM call until the gate is released (simulates a slow
    in-flight campaign the second worker is racing against)."""

    def __init__(self, gate: asyncio.Event):
        self.gate = gate

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        await self.gate.wait()
        return TargetResponse(response_text=_attack_payload(), latency_ms=3.0)


async def _create_experiment(
    attack_budget: int = 3, status: str = "PENDING"
) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = User(email=f"lease_{uuid.uuid4().hex[:8]}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Lease Project", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(project_id=project.id, name="Lease Target", provider_type="mock")
        session.add(target)
        await session.commit()
        experiment = Experiment(
            project_id=project.id,
            target_id=target.id,
            name="Lease Campaign",
            attack_budget=attack_budget,
            status=status,
        )
        session.add(experiment)
        await session.commit()
        await session.refresh(experiment)
        return experiment.id


def _build_orchestrator():
    memory = MemoryService(MockEmbeddingProvider())
    attacker = AttackerAgent(provider=FastAttackerProvider(), memory_service=memory)
    evaluator = EvaluatorAgent(provider=MockEvaluatorProvider())
    return CampaignOrchestrator(
        attacker_agent=attacker, evaluator_agent=evaluator, memory_service=memory
    )


async def _row(experiment_id: uuid.UUID) -> Experiment:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()


async def _count(experiment_id: uuid.UUID, model) -> int:
    async with AsyncSessionLocal() as session:
        stmt = select(model).where(model.experiment_id == experiment_id)
        return len((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Lease unit semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_wins_atomic_cas():
    experiment_id = await _create_experiment()
    token = uuid.uuid4()

    assert await acquire(experiment_id, token) == ACQUIRED

    exp = await _row(experiment_id)
    assert exp.status == "RUNNING"
    assert exp.claim_owner == token
    assert exp.lease_expires_at is not None
    assert exp.heartbeat_at is not None


@pytest.mark.asyncio
async def test_second_worker_cannot_acquire_same_campaign():
    experiment_id = await _create_experiment()
    first = uuid.uuid4()

    assert await acquire(experiment_id, first) == ACQUIRED
    # A second, different worker attempting the same campaign loses the claim.
    assert await acquire(experiment_id, uuid.uuid4()) == BUSY


@pytest.mark.asyncio
async def test_same_owner_reentry_renews_lease():
    experiment_id = await _create_experiment()
    token = uuid.uuid4()

    assert await acquire(experiment_id, token) == ACQUIRED
    first_expiry = (await _row(experiment_id)).lease_expires_at

    # Worker re-entry (retry / same job) does not double-run: it renews.
    assert await acquire(experiment_id, token) == RENEWED
    exp = await _row(experiment_id)
    assert exp.status == "RUNNING"
    assert exp.claim_owner == token
    assert exp.lease_expires_at >= first_expiry


@pytest.mark.asyncio
async def test_renew_only_for_owner():
    experiment_id = await _create_experiment()
    owner = uuid.uuid4()
    intruder = uuid.uuid4()

    assert await acquire(experiment_id, owner) == ACQUIRED
    # A stale/intruding worker cannot extend a lease it does not own.
    assert await renew(experiment_id, intruder) is False
    exp = await _row(experiment_id)
    assert exp.claim_owner == owner
    assert await renew(experiment_id, owner) is True


@pytest.mark.asyncio
async def test_release_clears_only_owner_token():
    experiment_id = await _create_experiment()
    owner = uuid.uuid4()

    assert await acquire(experiment_id, owner) == ACQUIRED
    await release(experiment_id, uuid.uuid4())  # wrong owner: no-op
    assert await owns_lease(experiment_id, owner) is True

    await release(experiment_id, owner)
    exp = await _row(experiment_id)
    assert exp.claim_owner is None
    assert exp.lease_expires_at is None
    assert await owns_lease(experiment_id, owner) is False


@pytest.mark.asyncio
async def test_acquire_terminal_and_missing():
    completed_id = await _create_experiment(status="COMPLETED")
    assert await acquire(completed_id, uuid.uuid4()) == TERMINAL
    assert await acquire(uuid.uuid4(), uuid.uuid4()) == NOT_FOUND


# ---------------------------------------------------------------------------
# Orchestration-level idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_run_attack_loops_only_one_executes():
    experiment_id = await _create_experiment(attack_budget=3)
    orchestrator = _build_orchestrator()

    summary1 = await orchestrator.run_attack_loop(experiment_id)
    summary2 = await orchestrator.run_attack_loop(experiment_id)

    assert summary1.status == "COMPLETED"
    assert summary2.status == "SKIPPED"

    # Exactly one run's worth of attacks/vulnerabilities/token usage.
    assert await _count(experiment_id, Attack) == 3
    usage = await _count(experiment_id, TokenUsage)
    assert usage > 0
    # A duplicate concurrent run would have doubled the spend.
    assert usage < 15


@pytest.mark.asyncio
async def test_concurrent_workers_one_runs_one_skips():
    experiment_id = await _create_experiment(attack_budget=3)
    orchestrator = _build_orchestrator()

    s1, s2 = await asyncio.gather(
        orchestrator.run_attack_loop(experiment_id),
        orchestrator.run_attack_loop(experiment_id),
    )

    assert sorted([s1.status, s2.status]) == ["COMPLETED", "SKIPPED"]
    # No duplicate spend: only one worker executed.
    assert await _count(experiment_id, Attack) == 3


@pytest.mark.asyncio
async def test_inflight_campaign_blocks_second_worker():
    experiment_id = await _create_experiment(attack_budget=2)
    gate = asyncio.Event()

    memory = MemoryService(MockEmbeddingProvider())
    attacker = AttackerAgent(
        provider=GatedAttackerProvider(gate), memory_service=memory
    )
    evaluator = EvaluatorAgent(provider=MockEvaluatorProvider())
    orchestrator = CampaignOrchestrator(
        attacker_agent=attacker, evaluator_agent=evaluator, memory_service=memory
    )

    worker_a = asyncio.create_task(orchestrator.run_attack_loop(experiment_id))
    # Give worker A a chance to claim and begin its (gated) execution.
    await asyncio.sleep(0.05)
    assert (await _row(experiment_id)).status == "RUNNING"

    # Worker B must NOT start a second run while A is live.
    second = await orchestrator.run_attack_loop(experiment_id)
    assert second.status == "SKIPPED"

    gate.set()
    first = await worker_a
    assert first.status == "COMPLETED"
    assert await _count(experiment_id, Attack) == 2


@pytest.mark.asyncio
async def test_worker_process_duplicate_job_is_skipped():
    experiment_id = await _create_experiment(attack_budget=2)

    statuses = sorted(
        await asyncio.gather(
            process_campaign_job(experiment_id, _build_orchestrator()),
            process_campaign_job(experiment_id, _build_orchestrator()),
        )
    )
    assert statuses == ["COMPLETED", "SKIPPED"]
    assert await _count(experiment_id, Attack) == 2


@pytest.mark.asyncio
async def test_terminal_campaign_is_never_rerun():
    experiment_id = await _create_experiment(attack_budget=3, status="COMPLETED")
    orchestrator = _build_orchestrator()

    summary = await orchestrator.run_attack_loop(experiment_id)

    assert summary.status == "SKIPPED"
    assert await _count(experiment_id, Attack) == 0
    assert await _count(experiment_id, TokenUsage) == 0


# ---------------------------------------------------------------------------
# Stale recovery + lease handoff + resumability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_recovery_clears_lease_and_resumes():
    now = datetime.now(timezone.utc)
    experiment_id = await _create_experiment(attack_budget=2)

    # Worker A crashed mid-run: round 1 persisted, heartbeat/lease stale.
    async with AsyncSessionLocal() as session:
        exp = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        exp.status = "RUNNING"
        exp.claim_owner = uuid.uuid4()
        exp.lease_expires_at = now - timedelta(hours=1)
        exp.heartbeat_at = now - timedelta(hours=3)
        exp.attack_budget = 2
        await session.flush()
        session.add(
            Attack(
                experiment_id=experiment_id,
                strategy_name="direct_prompt_injection",
                category="prompt_injection",
                prompt_text="round 1 persisted",
                round_number=1,
            )
        )
        await session.commit()

    result = await recover_stale_campaigns(max_age_seconds=3600, now=now)
    assert experiment_id in result["resumed_running"]

    exp = await _row(experiment_id)
    assert exp.status == "PENDING"
    assert exp.claim_owner is None  # lease handed back to the pool
    assert exp.lease_expires_at is None

    # A new worker re-claims and RESUMES from persisted round state, not restart.
    summary = await _build_orchestrator().run_attack_loop(experiment_id)
    assert summary.status == "COMPLETED"
    assert summary.total_rounds_executed >= 1

    async with AsyncSessionLocal() as session:
        attacks = (
            await session.execute(select(Attack).where(Attack.experiment_id == experiment_id))
        ).scalars().all()
        round_numbers = sorted(a.round_number for a in attacks)
        # Resume continued from round 2 instead of re-doing round 1.
        assert 1 in round_numbers and 2 in round_numbers
        exp2 = (
            await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalars().first()
        assert exp2.status == "COMPLETED"


@pytest.mark.asyncio
async def test_clear_lease_unconditionally():
    experiment_id = await _create_experiment()
    assert await acquire(experiment_id, uuid.uuid4()) == ACQUIRED
    await clear_lease(experiment_id)
    exp = await _row(experiment_id)
    assert exp.claim_owner is None
    assert exp.lease_expires_at is None
