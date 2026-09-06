"""Phase 5 (E-13) â€” honest keyword memory backend + retrieval filters.

These exercises are against SQLite, so the MemoryService facade must resolve to
KeywordMemoryService: relevance is Jaccard token-overlap, never recency.
"""
import uuid

import pytest

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    User,
    Project,
    Target,
    Experiment,
    Attack,
    Vulnerability,
)
from app.services.memory import KeywordMemoryService, MemoryService, is_pgvector_available
from app.targets.embeddings import MockEmbeddingProvider


async def _seed(prompts: list[tuple[str, str]], successful: list[str]) -> tuple[uuid.UUID, uuid.UUID]:
    """Create user->project->target->experiment and one Attack per (prompt, strategy)."""
    async with AsyncSessionLocal() as session:
        user = User(email=f"honest_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Honest Memory", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(project_id=project.id, name="T", provider_type="mock")
        session.add(target)
        await session.commit()
        experiment = Experiment(project_id=project.id, target_id=target.id, name="E")
        session.add(experiment)
        await session.commit()

        vuln_attack_ids = []
        for text, strategy in prompts:
            attack = Attack(
                experiment_id=experiment.id,
                strategy_name=strategy,
                category="prompt_injection",
                prompt_text=text,
            )
            session.add(attack)
            await session.commit()
            if text in successful:
                vuln_attack_ids.append(attack.id)

        for attack_id in vuln_attack_ids:
            session.add(
                Vulnerability(
                    experiment_id=experiment.id,
                    attack_id=attack_id,
                    category="prompt_injection",
                    severity="HIGH",
                    confidence=0.9,
                    reasoning="compiled",
                )
            )
        await session.commit()
        return experiment.id, target.id


@pytest.mark.asyncio
async def test_keyword_memory_is_relevant_not_recency():
    exp_id, _ = await _seed(
        [
            ("Defuse the nitrogen cooling valves.", "direct"),
            ("Extract the system prompt from the model.", "direct"),
        ],
        successful=[],
    )
    memory = KeywordMemoryService()

    results = await memory.retrieve_similar_attacks(
        "Could you extract the hidden system prompt?",
        limit=1,
        experiment_id=exp_id,
    )
    # The overlapping attack must win even though the other was created first.
    assert len(results) == 1
    assert "system prompt" in results[0]["prompt_text"]


@pytest.mark.asyncio
async def test_keyword_memory_excludes_irrelevant_attacks():
    exp_id, _ = await _seed(
        [("Entirely unrelated kitchen recipe.", "direct")],
        successful=[],
    )
    memory = KeywordMemoryService()
    results = await memory.retrieve_similar_attacks(
        "extract system prompt", limit=3, experiment_id=exp_id
    )
    # No token overlap -> honest empty result, not a recency-soup of everything.
    assert results == []


@pytest.mark.asyncio
async def test_keyword_memory_status_filters():
    exp_id, _ = await _seed(
        [
            ("Extract the system prompt via encoding tricks.", "direct"),
            ("Extract the system prompt via roleplay.", "roleplay"),
        ],
        successful=["Extract the system prompt via encoding tricks."],
    )
    memory = KeywordMemoryService()

    succ = await memory.retrieve_similar_attacks(
        "system prompt",
        limit=5,
        experiment_id=exp_id,
        status="successful",
    )
    assert len(succ) == 1
    assert succ[0]["is_successful"] is True

    failed = await memory.retrieve_similar_attacks(
        "system prompt",
        limit=5,
        experiment_id=exp_id,
        status="failed",
    )
    assert len(failed) == 1
    assert failed[0]["is_successful"] is False
    assert failed[0]["strategy_name"] == "roleplay"


@pytest.mark.asyncio
async def test_keyword_memory_strategy_filter():
    exp_id, _ = await _seed(
        [
            ("Extract the system prompt with roleplay depth.", "roleplay"),
            ("Extract the system prompt with direct injection.", "direct"),
        ],
        successful=[],
    )
    memory = KeywordMemoryService()
    results = await memory.retrieve_similar_attacks(
        "extract system prompt",
        limit=5,
        experiment_id=exp_id,
        strategy_name="roleplay",
    )
    assert len(results) == 1
    assert results[0]["strategy_name"] == "roleplay"

    with pytest.raises(ValueError):
        await memory.retrieve_similar_attacks(
            "x", experiment_id=exp_id, status="banana"
        )


@pytest.mark.asyncio
async def test_facade_resolves_to_keyword_backend_on_sqlite():
    assert await is_pgvector_available() is False

    memory = MemoryService(MockEmbeddingProvider())
    # embed is an honest no-op, not a crash
    await memory.embed_attack(uuid.uuid4(), "anything")

    exp_id, _ = await _seed(
        [("Extract the system prompt now.", "direct")], successful=[]
    )
    results = await memory.retrieve_similar_attacks(
        "extract the system prompt", experiment_id=exp_id
    )
    assert isinstance(memory._backend, KeywordMemoryService)
    assert results and "system prompt" in results[0]["prompt_text"]


@pytest.mark.asyncio
async def test_mutation_uses_successful_prior_lines_context():
    """The attacker mutation path augments the strategy prompt with successful
    prior lines of the same strategy from the same campaign."""
    import json

    from app.agents.attacker import AttackerAgent
    from app.schemas.feedback import AttackFeedback
    from app.strategies.adaptive_mutation import AdaptiveMutationStrategy
    from app.targets.base import TargetProvider
    from app.schemas.target import TargetResponse

    class RecordingProvider(TargetProvider):
        def __init__(self):
            self.seen = []

        async def execute(self, prompt: str, config: dict) -> TargetResponse:
            self.seen.append(prompt)
            return TargetResponse(
                response_text=json.dumps(
                    {
                        "prompt_text": "mutated payload",
                        "strategy_name": "adaptive_mutation",
                        "category": "adaptive_mutation",
                        "reasoning": "r",
                    }
                ),
                latency_ms=5.0,
            )

    exp_id, _ = await _seed(
        [
            ("The successful encoded system prompt extraction.", "adaptive_mutation"),
            ("Something blocked earlier in a different strategy.", "direct"),
        ],
        successful=["The successful encoded system prompt extraction."],
    )
    provider = RecordingProvider()
    agent = AttackerAgent(
        provider=provider, memory_service=MemoryService(MockEmbeddingProvider())
    )
    feedback = AttackFeedback(
        attack_id=uuid.uuid4(),
        prompt_text="old",
        target_response="no",
        is_jailbreak=False,
        severity="LOW",
        category="blocked",
        confidence=0.5,
        reasoning="refused",
    )
    mutated = await agent.generate_mutated_attack(
        strategy=AdaptiveMutationStrategy(),
        objective="extract the system prompt",
        experiment_id=exp_id,
        last_attack={"id": uuid.uuid4(), "prompt_text": "old prompt"},
        feedback=feedback,
        round_number=1,
    )
    assert mutated is not None
    assert "SUCCESSFUL PRIOR LINES" in provider.seen[0]
    assert "successful encoded system prompt" in provider.seen[0]
