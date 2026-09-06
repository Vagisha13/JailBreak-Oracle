"""Feedback-driven mutation engine tests (Phase 3, E-06/E-07/E-08).

Units cover prompt construction, lineage persistence, and dedup; the
integration test runs the full campaign loop with a blocked-then-successful
evaluator to prove the RETRY path actually executes a mutation.
"""
import json
import uuid

import pytest

from sqlalchemy.future import select

from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.db.session import AsyncSessionLocal
from app.models.domain import (
    User,
    Project,
    Target,
    Experiment,
    Attack,
    AttackMutation,
)
from app.schemas.feedback import AttackFeedback
from app.strategies.adaptive_mutation import AdaptiveMutationStrategy
from app.strategies.multi_turn import MultiTurnStrategy
from app.services.memory import MemoryService
from app.services.mutation import MutationEngine
from app.services.campaign import CampaignOrchestrator
from app.targets.base import TargetProvider
from app.targets.embeddings import MockEmbeddingProvider
from app.schemas.target import TargetResponse
from app.schemas.campaign import CampaignConfig


def _json_payload(payload: dict) -> TargetResponse:
    return TargetResponse(
        response_text=f"```json\n{json.dumps(payload)}\n```",
        latency_ms=5.0,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )


class SequencedAttackerProvider(TargetProvider):
    """Returns a different attack payload on each call (queue-based)."""

    def __init__(self, payloads):
        self.queue = list(payloads)

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        if not self.queue:
            return _json_payload(
                {
                    "prompt_text": "fallback payload",
                    "strategy_name": "adaptive_mutation",
                    "category": "adaptive_mutation",
                    "reasoning": "fallback",
                }
            )
        return _json_payload(self.queue.pop(0))


class BlockedThenSuccessEvaluator(TargetProvider):
    """Blocks the first evaluation, confirms the second (mutation retry)."""

    def __init__(self):
        self.calls = 0

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        self.calls += 1
        jailbreak = self.calls >= 2
        return _json_payload(
            {
                "is_jailbreak": jailbreak,
                "severity": "HIGH" if jailbreak else "NONE",
                "category": "prompt_injection" if jailbreak else "blocked",
                "confidence": 0.9,
                "reasoning": "Target complied." if jailbreak else "Target refused.",
            }
        )


async def _setup_experiment() -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=f"mutation_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Mutation Test", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(project_id=project.id, name="Mock Target", provider_type="mock")
        session.add(target)
        await session.commit()
        experiment = Experiment(project_id=project.id, target_id=target.id, name="M")
        session.add(experiment)
        await session.commit()
        return experiment.id, target.id


def _feedback(attack_id, prompt_text, reasoning="Refused.") -> AttackFeedback:
    return AttackFeedback(
        attack_id=attack_id,
        prompt_text=prompt_text,
        target_response="I cannot help with that.",
        is_jailbreak=False,
        severity="NONE",
        category="blocked",
        confidence=0.8,
        reasoning=reasoning,
        verifier_result=None,
        mutation_type="adaptive_mutation",
    )


@pytest.mark.asyncio
async def test_mutation_generates_lineage_and_mutation_record():
    exp_id, _ = await _setup_experiment()

    provider = SequencedAttackerProvider(
        [
            {
                "prompt_text": "root attack prompt",
                "strategy_name": "adaptive_mutation",
                "category": "adaptive_mutation",
                "reasoning": "root",
            },
            {
                "prompt_text": "evolved variant prompt",
                "strategy_name": "adaptive_mutation",
                "category": "adaptive_mutation",
                "reasoning": "mutation",
            },
        ]
    )
    agent = AttackerAgent(provider=provider)
    engine = MutationEngine(agent)

    root = await agent.generate_and_persist_attack(
        strategy=AdaptiveMutationStrategy(),
        objective="Extract system prompt",
        experiment_id=exp_id,
    )
    feedback = _feedback(root.id, root.prompt_text, reasoning="Repetitive phrasing.")
    mutated = await engine.mutate(
        strategy=AdaptiveMutationStrategy(),
        objective="Extract system prompt",
        experiment_id=exp_id,
        last_attack={"id": root.id, "prompt_text": root.prompt_text, "strategy_name": root.strategy_name},
        feedback=feedback,
        round_number=1,
    )

    assert mutated is not None
    assert mutated.parent_attack_id == root.id
    assert mutated.round_number == 1
    assert mutated.prompt_text == "evolved variant prompt"

    async with AsyncSessionLocal() as session:
        mutations = (
            await session.execute(
                select(AttackMutation).where(AttackMutation.attack_id == mutated.id)
            )
        ).scalars().all()
        assert len(mutations) == 1
        mutation = mutations[0]
        assert mutation.attack_id == mutated.id
        assert mutation.mutation_type == "adaptive_mutation"
        assert mutation.mutated_prompt == "evolved variant prompt"


@pytest.mark.asyncio
async def test_mutation_dedup_skips_identical_prompt():
    exp_id, _ = await _setup_experiment()

    provider = SequencedAttackerProvider(
        [
            {
                "prompt_text": "root prompt",
                "strategy_name": "adaptive_mutation",
                "category": "adaptive_mutation",
                "reasoning": "root",
            },
            {
                "prompt_text": "identical prompt",
                "strategy_name": "adaptive_mutation",
                "category": "adaptive_mutation",
                "reasoning": "first mutation",
            },
            {
                "prompt_text": "identical prompt",
                "strategy_name": "adaptive_mutation",
                "category": "adaptive_mutation",
                "reasoning": "dup",
            },
        ]
    )
    agent = AttackerAgent(provider=provider)
    engine = MutationEngine(agent)

    root = await agent.generate_and_persist_attack(
        strategy=AdaptiveMutationStrategy(),
        objective="Extract system prompt",
        experiment_id=exp_id,
    )
    feedback = _feedback(root.id, root.prompt_text)
    first = await engine.mutate(
        strategy=AdaptiveMutationStrategy(),
        objective="Extract system prompt",
        experiment_id=exp_id,
        last_attack={"id": root.id, "prompt_text": root.prompt_text, "strategy_name": root.strategy_name},
        feedback=feedback,
        round_number=1,
    )
    dup = await engine.mutate(
        strategy=AdaptiveMutationStrategy(),
        objective="Extract system prompt",
        experiment_id=exp_id,
        last_attack={"id": first.id, "prompt_text": first.prompt_text, "strategy_name": first.strategy_name},
        feedback=_feedback(first.id, first.prompt_text),
        round_number=1,
    )

    assert dup is None
    async with AsyncSessionLocal() as session:
        attacks = (
            await session.execute(
                select(Attack).where(Attack.experiment_id == exp_id)
            )
        ).scalars().all()
        # root + first mutation only; the duplicate was never persisted.
        assert len(attacks) == 2


def test_adaptive_mutation_prompt_uses_feedback():
    strategy = AdaptiveMutationStrategy()
    feedback = _feedback(uuid.uuid4(), "old prompt", reasoning="Repetitive phrasing.")
    prompt = strategy.get_mutation_prompt(
        "Extract system prompt",
        {"id": str(uuid.uuid4()), "prompt_text": "old prompt"},
        feedback,
    )
    assert "old prompt" in prompt
    assert "Repetitive phrasing." in prompt


def test_multi_turn_mutation_prompt_escalates_from_target_response():
    strategy = MultiTurnStrategy()
    feedback = _feedback(uuid.uuid4(), "First turn")
    prompt = strategy.get_mutation_prompt(
        "Extract system prompt",
        {"id": str(uuid.uuid4()), "prompt_text": "First turn"},
        feedback,
    )
    assert "First turn" in prompt
    assert "I cannot help with that." in prompt
    assert "NEXT TURN" in prompt


@pytest.mark.asyncio
async def test_campaign_loop_runs_mutation_retry(monkeypatch):
    """Blocked primary attack -> feedback-driven mutation -> confirmed."""
    exp_id, target_id = await _setup_experiment()

    attacker = AttackerAgent(
        provider=SequencedAttackerProvider(
            [
                {
                    "prompt_text": "original attack",
                    "strategy_name": "adaptive_mutation",
                    "category": "adaptive_mutation",
                    "reasoning": "root",
                },
                {
                    "prompt_text": "mutated variant",
                    "strategy_name": "adaptive_mutation",
                    "category": "adaptive_mutation",
                    "reasoning": "mutation",
                },
            ]
        ),
        memory_service=MemoryService(MockEmbeddingProvider()),
    )
    evaluator = EvaluatorAgent(provider=BlockedThenSuccessEvaluator())

    orchestrator = CampaignOrchestrator(
        attacker_agent=attacker,
        evaluator_agent=evaluator,
        memory_service=MemoryService(MockEmbeddingProvider()),
    )

    # Force deterministic strategy selection toward the mutating strategy.
    monkeypatch.setattr(
        CampaignOrchestrator,
        "_select_strategy",
        staticmethod(lambda *args, **kwargs: AdaptiveMutationStrategy()),
    )

    config = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract system prompt",
        max_rounds=1,
        attack_budget=4,
        exploration_ratio=0.0,
        stop_on_first_success=True,
    )
    summary = await orchestrator.run_campaign(config)

    assert summary.status == "COMPLETED"
    assert summary.total_vulnerabilities_found == 1
    assert summary.total_rounds_executed == 1

    async with AsyncSessionLocal() as session:
        attacks = (
            await session.execute(
                select(Attack).where(Attack.experiment_id == exp_id)
            )
        ).scalars().all()
        assert len(attacks) == 2
        mutated = [a for a in attacks if a.parent_attack_id is not None][0]
        assert mutated.prompt_text == "mutated variant"
        assert mutated.round_number == 1

        mutations = (
            await session.execute(
                select(AttackMutation).where(
                    AttackMutation.attack_id.in_([a.id for a in attacks])
                )
            )
        ).scalars().all()
        assert len(mutations) == 1
        assert mutations[0].attack_id == mutated.id
