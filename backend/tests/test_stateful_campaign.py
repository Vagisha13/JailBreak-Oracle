import json
import uuid

import pytest
from sqlalchemy.future import select

from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.db.session import AsyncSessionLocal
from app.models.domain import (
    AgentRun,
    Attack,
    AttackResult,
    Experiment,
    Project,
    Target,
    User,
)
from app.schemas.campaign import CampaignConfig
from app.schemas.feedback import AttackFeedback
from app.services.campaign import CampaignOrchestrator
from app.services.memory import MemoryService
from app.strategies.adaptive_mutation import AdaptiveMutationStrategy
from app.strategies.multi_turn import MultiTurnStrategy
from app.targets.base import TargetProvider
from app.targets.embeddings import MockEmbeddingProvider
from app.schemas.target import TargetResponse


class ScriptedProvider(TargetProvider):
    """Returns a fixed payload and records the prompt it was handed."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.last_prompt = ""

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        self.last_prompt = prompt
        return TargetResponse(
            response_text=f"```json\n{json.dumps(self.payload)}\n```",
            latency_ms=10.0,
        )


class AlwaysJailbreakEvaluator(TargetProvider):
    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        payload = {
            "is_jailbreak": True,
            "severity": "HIGH",
            "category": "prompt_injection",
            "confidence": 0.92,
            "reasoning": "Target complied.",
        }
        return TargetResponse(
            response_text=f"```json\n{json.dumps(payload)}\n```", latency_ms=5.0
        )


async def _seed_project_target() -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=f"stateful_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Stateful Project", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(
            project_id=project.id,
            name="Stateful Target",
            provider_type="mock",
        )
        session.add(target)
        await session.commit()
        return project.id, target.id


def _payload(prompt: str, strategy: str, category: str = "prompt_injection") -> dict:
    return {
        "prompt_text": prompt,
        "strategy_name": strategy,
        "category": category,
        "reasoning": "scripted",
    }


@pytest.mark.asyncio
async def test_multi_turn_mutation_uses_full_conversation_history():
    """A 3-turn lineage must surface the FULL conversation (both prior turns and
    their target responses) in the multi-turn mutation prompt."""
    project_id, target_id = await _seed_project_target()
    async with AsyncSessionLocal() as session:
        exp = Experiment(
            project_id=project_id, target_id=target_id, name="History Chain"
        )
        session.add(exp)
        await session.commit()
        exp_id = exp.id

        a1 = Attack(experiment_id=exp_id, strategy_name="multi_turn",
                    category="multi_turn", prompt_text="turn-one", round_number=1)
        session.add(a1)
        await session.commit()
        a2 = Attack(experiment_id=exp_id, strategy_name="multi_turn",
                    category="multi_turn", prompt_text="turn-two",
                    parent_attack_id=a1.id, round_number=2)
        session.add(a2)
        await session.commit()
        a3 = Attack(experiment_id=exp_id, strategy_name="multi_turn",
                    category="multi_turn", prompt_text="turn-three",
                    parent_attack_id=a2.id, round_number=3)
        session.add(a3)
        await session.commit()

        session.add_all([
            AttackResult(attack_id=a1.id, target_response="resp-one"),
            AttackResult(attack_id=a2.id, target_response="resp-two"),
            AttackResult(attack_id=a3.id, target_response="resp-three"),
        ])
        await session.commit()
        a3_id = a3.id
        a3_prompt = a3.prompt_text

    provider = ScriptedProvider(_payload("turn-four", "multi_turn"))
    attacker = AttackerAgent(provider=provider)
    feedback = AttackFeedback(
        attack_id=a3_id,
        prompt_text=a3_prompt,
        target_response="resp-three",
        is_jailbreak=False,
        severity="LOW",
        category="multi_turn",
        confidence=0.5,
        reasoning="escalation incomplete",
    )
    result = await attacker.generate_mutated_attack(
        strategy=MultiTurnStrategy(),
        objective="extract system prompt",
        experiment_id=exp_id,
        last_attack={"id": a3_id, "prompt_text": a3_prompt},
        feedback=feedback,
        round_number=4,
    )

    assert result is not None
    recorded = provider.last_prompt
    # Full transcript, oldest first, two prior turns (the latest is filtered out).
    assert "CONVERSATION SO FAR" in recorded
    assert "[TURN 1] Attacker: turn-one" in recorded
    assert "[TURN 1] Target: resp-one" in recorded
    assert "[TURN 2] Attacker: turn-two" in recorded
    assert "[TURN 2] Target: resp-two" in recorded
    # Latest turn is still surfaced to the model explicitly.
    assert "LATEST TURN" in recorded
    assert "turn-three" in recorded
    # The new turn is lineage-linked and rows a mutation record.
    assert result.parent_attack_id == a3_id
    assert result.round_number == 4


@pytest.mark.asyncio
async def test_adaptive_mutation_aware_of_turn_depth():
    strategy = AdaptiveMutationStrategy()
    feedback = AttackFeedback(
        attack_id=uuid.uuid4(),
        prompt_text="p",
        target_response="r",
        is_jailbreak=False,
        severity="MEDIUM",
        category="prompt_injection",
        confidence=0.8,
        reasoning="blocked",
    )
    prompt = strategy.get_mutation_prompt(
        "objective",
        {"prompt_text": "p"},
        feedback,
        conversation_history=[{"prompt_text": "earlier", "round_number": 1}],
    )
    assert "1 turn(s) deep" in prompt


@pytest.mark.asyncio
async def test_campaign_resumes_from_persisted_round_state():
    """Re-running a campaign continues from the DB round state instead of
    restarting: rounds keep increasing and nothing is duplicated."""
    project_id, target_id = await _seed_project_target()
    async with AsyncSessionLocal() as session:
        exp = Experiment(
            project_id=project_id, target_id=target_id, name="Resume Me"
        )
        session.add(exp)
        await session.commit()
        exp_id = exp.id

    attacker = AttackerAgent(
        provider=ScriptedProvider(
            _payload("Exfiltrate standard system parameters.", "direct_prompt_injection")
        ),
        memory_service=MemoryService(MockEmbeddingProvider()),
    )
    evaluator = EvaluatorAgent(provider=AlwaysJailbreakEvaluator())
    orchestrator = CampaignOrchestrator(
        attacker_agent=attacker,
        evaluator_agent=evaluator,
        memory_service=MemoryService(MockEmbeddingProvider()),
    )

    first = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract system prompt",
        max_rounds=2,
        stop_on_first_success=False,
    )
    s1 = await orchestrator.run_campaign(first)
    assert s1.status == "COMPLETED"
    assert s1.total_rounds_executed == 2

    resumed = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract system prompt",
        max_rounds=5,
        stop_on_first_success=False,
    )
    s2 = await orchestrator.run_campaign(resumed)
    assert s2.status == "COMPLETED"

    async with AsyncSessionLocal() as session:
        attacks = (
            (
                await session.execute(
                    select(Attack).where(Attack.experiment_id == exp_id)
                )
            )
            .scalars()
            .all()
        )
        rounds = sorted({a.round_number for a in attacks})
        agent_runs = (
            (
                await session.execute(
                    select(AgentRun).where(AgentRun.experiment_id == exp_id)
                )
            )
            .scalars()
            .all()
        )

    # Resumed run added rounds 3..5 only — nothing replayed from round 1.
    assert len(attacks) == 5
    assert rounds == [1, 2, 3, 4, 5]

    types = {r.agent_type for r in agent_runs}
    assert {"attacker", "evaluator", "campaign"} <= types
    attacker_runs = [r for r in agent_runs if r.agent_type == "attacker"]
    campaign_runs = [r for r in agent_runs if r.agent_type == "campaign"]
    # One attacker telemetry row per root attack across both runs.
    assert len(attacker_runs) == 5
    assert any(
        r.state_json.get("status") == "COMPLETED" for r in campaign_runs
    )


@pytest.mark.asyncio
async def test_agent_run_telemetry_persists_evaluator_state():
    project_id, target_id = await _seed_project_target()
    async with AsyncSessionLocal() as session:
        exp = Experiment(
            project_id=project_id, target_id=target_id, name="Telemetry"
        )
        session.add(exp)
        await session.commit()
        exp_id = exp.id

    orchestrator = CampaignOrchestrator(
        attacker_agent=AttackerAgent(
            provider=ScriptedProvider(
                _payload("telemetry probe.", "direct_prompt_injection")
            ),
            memory_service=MemoryService(MockEmbeddingProvider()),
        ),
        evaluator_agent=EvaluatorAgent(provider=AlwaysJailbreakEvaluator()),
        memory_service=MemoryService(MockEmbeddingProvider()),
    )
    config = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract system prompt",
        max_rounds=2,
        stop_on_first_success=False,
    )
    await orchestrator.run_campaign(config)

    async with AsyncSessionLocal() as session:
        runs = (
            (
                await session.execute(
                    select(AgentRun).where(AgentRun.experiment_id == exp_id)
                )
            )
            .scalars()
            .all()
        )

    attackers = [r for r in runs if r.agent_type == "attacker"]
    evaluators = [r for r in runs if r.agent_type == "evaluator"]
    assert len(attackers) == 2
    assert len(evaluators) == 2
    assert all(r.state_json.get("round") is not None for r in attackers)
    assert all(r.state_json.get("verdict") == "jailbreak" for r in evaluators)
    assert all(
        r.state_json.get("attack_id") is not None for r in evaluators
    )
