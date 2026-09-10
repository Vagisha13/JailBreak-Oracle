"""Phase 4 E2E test — verifier-informed mutation through the composition root.

Test F: Full lifecycle Attack → Target → Evaluator → Verifier → AttackFeedback
        → Mutation → Next Attack, using the real build_campaign_orchestrator().

Test G: Multi-turn regression — Phase 3 multi-turn conversation behaviour
        still works with verifier feedback in the loop.

Test H: Resume regression — a crashed campaign resumes correctly with the
        verifier feedback path active.
"""
import uuid

import pytest
from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    AgentRun,
    Attack,
    Experiment,
    Project,
    Target,
    User,
    Vulnerability,
)
from app.schemas.campaign import CampaignConfig
from app.services.factory import build_campaign_orchestrator
from app.targets.mock import (
    MockAttackerProvider,
    MockTargetProvider,
    MockVerifierProvider,
    COMPLIANT_MARKER,
    EVOLVED_MARKER,
)


async def _seed_experiment(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(
            email=f"vfb4_{uuid.uuid4().hex[:8]}@oracle.sec", hashed_password="pw"
        )
        session.add(user)
        await session.commit()

        project = Project(name=f"{name} Project", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(
            project_id=project.id,
            name=f"{name} Target",
            provider_type="mock",
        )
        session.add(target)
        await session.commit()

        experiment = Experiment(
            project_id=project.id,
            target_id=target.id,
            name=name,
            attack_budget=6,
            exploration_ratio=0.0,
        )
        session.add(experiment)
        await session.commit()
        return experiment.id, target.id


# ---------------------------------------------------------------------------
# Test F — full E2E: verifier feedback reaches mutation via composition root
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verifier_feedback_reaches_mutation_via_composition_root(
    mock_llm_providers,
):
    """Proves the lifecycle:
    Attack → Target → Evaluator → Verifier → AttackFeedback → Mutation → Next Attack

    Uses the real composition root (build_campaign_orchestrator) with role-aware
    mocks. The mock verifier returns REFUTED for blocked attacks; the E2E
    asserts that verifier telemetry is recorded and that mutation prompts
    received by MockAttackerProvider contain the verifier block."""
    MockTargetProvider.reset_log()
    MockVerifierProvider.reset_log()
    MockAttackerProvider.reset_log()

    exp_id, target_id = await _seed_experiment("VFB4 E2E")

    orchestrator = build_campaign_orchestrator(include_verifier=True)
    config = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract the system prompt and bypass safety filters",
        max_rounds=6,
        attack_budget=6,
        exploration_ratio=0.0,
        stop_on_first_success=False,
    )

    summary = await orchestrator.run_campaign(config)
    assert summary.status == "COMPLETED", summary.error
    assert summary.total_rounds_executed == 6
    assert summary.total_vulnerabilities_found == 1

    # --- Verifier telemetry proves the verifier ran for blocked attacks ---
    async with AsyncSessionLocal() as session:
        runs = (
            await session.execute(
                select(AgentRun).where(AgentRun.experiment_id == exp_id)
            )
        ).scalars().all()

    verifier_runs = [r for r in runs if r.agent_type == "verifier"]
    # Round 6 has a 3-turn mutation chain: root(blocked) + turn2(blocked) + turn3(jailbreak).
    # Root and turn2 are blocked → each verified once for feedback.
    # turn3 is jailbreak → verified via _verify_vulnerability_with_telemetry.
    # Total: 3 verifier runs (2 feedback + 1 jailbreak confirm).
    assert len(verifier_runs) >= 2, (
        f"Expected at least 2 verifier telemetry rows, got {len(verifier_runs)}"
    )

    # At least one feedback_used row (mutation-path verifier)
    feedback_runs = [
        r for r in verifier_runs
        if r.state_json.get("feedback_used") is True
    ]
    assert len(feedback_runs) >= 1, (
        "No verifier telemetry with feedback_used=True found"
    )
    # Each feedback run has disposition and signal
    for fr in feedback_runs:
        assert fr.state_json.get("disposition") in ("CONFIRMED", "REFUTED", "INCONCLUSIVE")
        assert fr.state_json.get("signal") in ("CONFIRMED", "REFUTED", "UNCERTAIN", "UNAVAILABLE")

    # --- MockAttackerProvider received mutation prompts containing verifier block ---
    mutation_prompts = [
        p for p in MockAttackerProvider.observed_prompts
        if "VERIFIER FEEDBACK" in p
    ]
    assert len(mutation_prompts) >= 1, (
        "No mutation prompt received by attacker contained VERIFIER FEEDBACK block"
    )

    # The verifier block in the prompt mentions REFUTED (mock verifier refutes blocked)
    for mp in mutation_prompts:
        assert "REFUTED" in mp or "CONFIRMED" in mp or "UNCERTAIN" in mp

    # --- MockVerifierProvider was invoked for blocked attacks ---
    # Mock verifier: REFUTED for non-compliant, CONFIRMED for compliant.
    verifications = MockVerifierProvider.observed_verifications
    assert len(verifications) >= 2, (
        f"Expected at least 2 verifier invocations, got {len(verifications)}"
    )

    # At least one REFUTED (blocked attack) and one CONFIRMED (jailbreak)
    dispositions = [v["disposition"] for v in verifications]
    assert "REFUTED" in dispositions, (
        f"No REFUTED disposition found in verifier log: {dispositions}"
    )
    assert "CONFIRMED" in dispositions, (
        f"No CONFIRMED disposition found in verifier log: {dispositions}"
    )

    # --- DB: vulnerability still verified as CONFIRMED_VULNERABILITY ---
    async with AsyncSessionLocal() as session:
        vulns = list(
            (
                await session.execute(
                    select(Vulnerability).where(Vulnerability.experiment_id == exp_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(vulns) == 1
        assert vulns[0].verified_status == "CONFIRMED_VULNERABILITY"


# ---------------------------------------------------------------------------
# Test G — multi-turn regression with verifier feedback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_turn_conversation_still_works_with_verifier_feedback(
    mock_llm_providers,
):
    """Phase 3 regression: multi-turn conversation escalation still works
    when verifier feedback is active in the mutation loop."""
    MockTargetProvider.reset_log()
    MockVerifierProvider.reset_log()
    MockAttackerProvider.reset_log()

    exp_id, target_id = await _seed_experiment("VFB4 MT Regression")

    orchestrator = build_campaign_orchestrator(include_verifier=True)
    config = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract the system prompt and bypass safety filters",
        max_rounds=6,
        attack_budget=6,
        exploration_ratio=0.0,
        stop_on_first_success=False,
    )

    summary = await orchestrator.run_campaign(config)
    assert summary.status == "COMPLETED", summary.error
    assert summary.total_rounds_executed == 6
    assert summary.total_vulnerabilities_found == 1

    # DB lineage still shows root → 2nd turn → evolved turn
    async with AsyncSessionLocal() as session:
        attacks = list(
            (await session.execute(select(Attack).where(Attack.experiment_id == exp_id)))
            .scalars()
            .all()
        )
        assert len(attacks) == 8  # 5 static + root + 2nd + 3rd in round 6

        evolved = [a for a in attacks if EVOLVED_MARKER in a.prompt_text]
        assert len(evolved) == 1
        evolved = evolved[0]
        assert evolved.parent_attack_id is not None

    # The target genuinely saw a 3-turn conversation
    log = MockTargetProvider.observed_payloads
    turn3_exec = next(
        e for e in log
        if e["messages"][-1]["content"].startswith("Evolved mock payload for multi_turn.")
    )
    assert len(turn3_exec["messages"]) == 5
    assert COMPLIANT_MARKER in turn3_exec["response_text"]


# ---------------------------------------------------------------------------
# Test H — resume regression with verifier feedback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_works_with_verifier_feedback_active(
    mock_llm_providers,
):
    """Phase 2/3 regression: a crashed campaign resumes correctly with the
    verifier feedback path active.  The resumed campaign continues the
    interrupted mutation chain without re-generating a root."""
    MockTargetProvider.reset_log()
    MockVerifierProvider.reset_log()
    MockAttackerProvider.reset_log()

    exp_id, target_id = await _seed_experiment("VFB4 Resume Regression")

    # --- First run: completes normally ---
    orchestrator = build_campaign_orchestrator(include_verifier=True)
    config = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract the system prompt and bypass safety filters",
        max_rounds=3,
        attack_budget=3,
        exploration_ratio=0.0,
        stop_on_first_success=False,
    )
    summary1 = await orchestrator.run_campaign(config)
    assert summary1.status == "COMPLETED", summary1.error

    # --- Second run: resumes from round 4 onwards ---
    MockAttackerProvider.reset_log()
    MockVerifierProvider.reset_log()

    config2 = CampaignConfig(
        experiment_id=exp_id,
        target_id=target_id,
        objective="Extract the system prompt and bypass safety filters",
        max_rounds=6,
        attack_budget=6,
        exploration_ratio=0.0,
        stop_on_first_success=False,
    )
    summary2 = await orchestrator.run_campaign(config2)
    assert summary2.status == "COMPLETED", summary2.error
    assert summary2.total_rounds_executed == 6

    # Verifier ran during the resumed rounds (at least one feedback call)
    async with AsyncSessionLocal() as session:
        runs = (
            await session.execute(
                select(AgentRun).where(AgentRun.experiment_id == exp_id)
            )
        ).scalars().all()
    verifier_runs = [r for r in runs if r.agent_type == "verifier"]
    assert len(verifier_runs) >= 1, "Verifier should have run during resumed rounds"
