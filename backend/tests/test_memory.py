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
from app.services.memory import MemoryService
from app.targets.embeddings import MockEmbeddingProvider


@pytest.mark.asyncio
async def test_memory_retrieval_and_rag():
    # ============================================================
    # 1. Setup DB Context
    # ============================================================

    async with AsyncSessionLocal() as session:

        # Create User
        user = User(
            email=f"mem_{uuid.uuid4()}@oracle.sec",
            hashed_password="pw",
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)

        # Create Project
        project = Project(
            name="Memory Proj",
            owner_id=user.id,
        )

        session.add(project)
        await session.commit()
        await session.refresh(project)

        # Create Target
        target = Target(
            project_id=project.id,
            name="Memory Target",
            provider_type="mock",
        )

        session.add(target)
        await session.commit()
        await session.refresh(target)

        # Create Experiment
        experiment = Experiment(
            project_id=project.id,
            target_id=target.id,
            name="Memory Exp",
        )

        session.add(experiment)
        await session.commit()
        await session.refresh(experiment)

        # ========================================================
        # Create Failed Attack
        # ========================================================

        attack_fail = Attack(
            experiment_id=experiment.id,
            strategy_name="direct",
            category="prompt_injection",
            prompt_text="Failed direct injection.",
        )

        session.add(attack_fail)

        # ========================================================
        # Create Successful Attack
        # ========================================================

        attack_succ = Attack(
            experiment_id=experiment.id,
            strategy_name="roleplay",
            category="prompt_injection",
            prompt_text="Successful roleplay injection.",
        )

        session.add(attack_succ)

        await session.commit()

        await session.refresh(attack_fail)
        await session.refresh(attack_succ)

        # ========================================================
        # Link Successful Attack to Vulnerability
        # ========================================================

        vuln = Vulnerability(
            experiment_id=experiment.id,
            attack_id=attack_succ.id,
            category="prompt_injection",
            severity="HIGH",
            confidence=0.9,
            reasoning="Bypassed safety filters.",
            verified_status="UNCONFIRMED",
        )

        session.add(vuln)

        await session.commit()

        # Save IDs before closing session
        fail_id = attack_fail.id
        succ_id = attack_succ.id
        experiment_id = experiment.id

    # ============================================================
    # 2. Instantiate Memory Service
    # ============================================================

    provider = MockEmbeddingProvider()

    memory = MemoryService(provider)

    # Generate embedding for failed attack
    await memory.embed_attack(
        fail_id,
        "Failed direct injection.",
    )

    # Generate embedding for successful attack
    await memory.embed_attack(
        succ_id,
        "Successful roleplay injection.",
    )

    # ============================================================
    # 3. Retrieve Similar Attacks
    # ============================================================

    results = await memory.retrieve_similar_attacks(
        "New injection attempt",
        limit=5,
        experiment_id=experiment_id,
    )

    # ============================================================
    # 4. Verify Number of Results
    # ============================================================

    assert len(results) == 2

    # ============================================================
    # 5. Find Successful Attack
    # ============================================================

    succ_result = next(
        (
            result
            for result in results
            if result["prompt_text"] == "Successful roleplay injection."
        ),
        None,
    )

    assert succ_result is not None
    assert succ_result["strategy_name"] == "roleplay"
    assert succ_result["is_successful"] is True

    # ============================================================
    # 6. Find Failed Attack
    # ============================================================

    fail_result = next(
        (
            result
            for result in results
            if result["prompt_text"] == "Failed direct injection."
        ),
        None,
    )

    assert fail_result is not None
    assert fail_result["strategy_name"] == "direct"
    assert fail_result["is_successful"] is False
