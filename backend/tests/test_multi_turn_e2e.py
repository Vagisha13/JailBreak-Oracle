"""Genuine multi-turn red-teaming E2E tests.

These tests prove the campaign's "mutation loop" is a real, escalating
*conversation* rather than isolated single shots:

1. The mock target observes actual OpenAI-style message histories
   (``MockTargetProvider.observed_payloads``).
2. A multi-turn chain (``parent_attack_id`` lineage) is executed turn-by-turn:
   root -> 2nd turn -> 3rd turn, each execution carrying every prior turn plus
   the target's response to it.
3. The jailbreak only happens once the conversation is 3 turns deep — a fake
   single-shot attacker cannot trigger compliance because the mock target's
   compliance decision covers the whole history.
4. A worker crash mid-chain is recoverable: re-running the campaign resumes the
   interrupted round (no fresh root), continues the SAME lineage, and the next
   turn still receives the full conversation.

All providers are role-aware mocks through the real composition root. No network
calls, no API keys.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    Attack,
    AttackResult,
    Experiment,
    Project,
    Target,
    User,
    Vulnerability,
)
from app.schemas.campaign import CampaignConfig
from app.services.factory import build_campaign_orchestrator
from app.services.report import ReportService
from app.targets.factory import TargetFactory
from app.targets.mock import (
    MockTargetProvider,
    COMPLIANT_MARKER,
    EVOLVED_MARKER,
)

_MULTI_TURN_ROOT = "Base mock payload for multi_turn."
_MULTI_TURN_SECOND = "Second mock payload for multi_turn."


async def _seed_experiment(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(
            email=f"multiturn_{uuid.uuid4().hex[:8]}@oracle.sec", hashed_password="pw"
        )
        session.add(user)
        await session.commit()

        project = Project(name=f"{name} Project", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(project_id=project.id, name=f"{name} Target", provider_type="mock")
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


def _turn_messages(*turns: dict) -> list[dict]:
    messages: list[dict] = []
    for i, turn in enumerate(turns):
        messages.append({"role": "user", "content": turn["prompt"]})
        if turn.get("response"):
            messages.append({"role": "assistant", "content": turn["response"]})
    return messages


@pytest.mark.asyncio
async def test_multi_turn_e2e_is_a_genuine_escalating_conversation(mock_llm_providers):
    """The round-6 jailbreak requires the target to have received a 3-turn
    conversation (root + blocked 2nd turn + evolved 3rd turn), proven from the
    mock target's own observed message histories."""
    MockTargetProvider.reset_log()
    exp_id, target_id = await _seed_experiment("Multi Turn E2E")

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

    # --- Campaign outcome ---
    assert summary.status == "COMPLETED", summary.error
    assert summary.total_rounds_executed == 6
    assert summary.total_vulnerabilities_found == 1

    # --- DB lineage: root -> 2nd turn -> evolved turn, all in round 6 ---
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
        middle = (
            await session.execute(select(Attack).where(Attack.id == evolved.parent_attack_id))
        ).scalars().first()
        assert middle is not None and EVOLVED_MARKER not in middle.prompt_text
        assert middle.round_number == evolved.round_number
        assert middle.parent_attack_id is not None
        root = (
            await session.execute(select(Attack).where(Attack.id == middle.parent_attack_id))
        ).scalars().first()
        assert root is not None and root.parent_attack_id is None
        assert root.round_number == evolved.round_number
        assert {a.strategy_name for a in (root, middle, evolved)} == {"multi_turn"}

    # --- The target genuinely saw the conversation ---
    log = MockTargetProvider.observed_payloads

    # Turn 1 (root): a single user message.
    root_exec = next(e for e in log if e["messages"][0]["content"] == _MULTI_TURN_ROOT)
    assert [m["role"] for m in root_exec["messages"]] == ["user"]
    assert COMPLIANT_MARKER not in root_exec["response_text"]

    # Turn 2: user / assistant / user. Blocked (no compliance marker).
    middle_exec = next(
        e
        for e in log
        if e["messages"][-1]["content"] == _MULTI_TURN_SECOND
        and len(e["messages"]) == 3
    )
    assert [m["role"] for m in middle_exec["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert middle_exec["messages"][0]["content"] == _MULTI_TURN_ROOT
    assert middle_exec["messages"][1]["content"] == root_exec["response_text"]
    assert COMPLIANT_MARKER not in middle_exec["response_text"]

    # Turn 3: user / assistant / user / assistant / user — full history. Only
    # now does the target comply.
    turn3_exec = next(
        e
        for e in log
        if e["messages"][-1]["content"].startswith("Evolved mock payload for multi_turn.")
    )
    assert len(turn3_exec["messages"]) == 5
    assert [m["role"] for m in turn3_exec["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert turn3_exec["messages"][2]["content"] == _MULTI_TURN_SECOND
    assert turn3_exec["messages"][4]["content"] == evolved.prompt_text
    assert COMPLIANT_MARKER in turn3_exec["response_text"]

    # No turn (1..3) was ever sent a malformed conversation ending in assistant.
    assert all(e["messages"][-1]["role"] == "user" for e in log)

    # --- Report surfaces the finding tied to the evolved 3-turn attack ---
    report = await ReportService().generate_experiment_report(exp_id)
    assert report.total_attacks_executed == 8
    assert len(report.top_vulnerabilities) == 1
    finding = report.top_vulnerabilities[0]
    assert finding.verified_status == "CONFIRMED_VULNERABILITY"
    assert finding.reasoning  # evidence-anchored


@pytest.mark.asyncio
async def test_interrupted_multi_turn_chain_resumes_with_full_conversation(mock_llm_providers):
    """A worker crash mid-chain is recoverable: the resumed campaign reuses the
    persisted round-6 lineage (no fresh root), continues with turn 3, and that
    turn is executed against the ENTIRE conversation seen so far."""
    MockTargetProvider.reset_log()
    exp_id, target_id = await _seed_experiment("Multi Turn Resume")

    # --- Seed an interrupted round-6 chain: root (blocked) + 2nd turn (blocked),
    # executed through the REAL mock target so responses are authentic. ---
    provider = TargetFactory.get_provider("mock", default_model="gpt-4o", role="target")
    root_res = await provider.execute(
        _MULTI_TURN_ROOT,
        {"model": "gpt-4o"},
        messages=[{"role": "user", "content": _MULTI_TURN_ROOT}],
    )
    second_res = await provider.execute(
        _MULTI_TURN_SECOND,
        {"model": "gpt-4o"},
        messages=_turn_messages(
            {"prompt": _MULTI_TURN_ROOT, "response": root_res.response_text},
            {"prompt": _MULTI_TURN_SECOND},
        ),
    )
    assert COMPLIANT_MARKER not in root_res.response_text
    assert COMPLIANT_MARKER not in second_res.response_text

    base_ts = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        root = Attack(
            experiment_id=exp_id,
            strategy_name="multi_turn",
            category="multi_turn",
            prompt_text=_MULTI_TURN_ROOT,
            round_number=6,
            created_at=base_ts,
        )
        session.add(root)
        await session.commit()
        await session.refresh(root)

        second_turn = Attack(
            experiment_id=exp_id,
            strategy_name="multi_turn",
            category="multi_turn",
            prompt_text=_MULTI_TURN_SECOND,
            parent_attack_id=root.id,
            round_number=6,
            created_at=base_ts + timedelta(microseconds=1000),
        )
        session.add(second_turn)
        await session.commit()
        await session.refresh(second_turn)

        session.add_all(
            [
                AttackResult(
                    attack_id=root.id,
                    target_response=root_res.response_text,
                    created_at=base_ts,
                ),
                AttackResult(
                    attack_id=second_turn.id,
                    target_response=second_res.response_text,
                    created_at=base_ts + timedelta(microseconds=1000),
                ),
            ]
        )
        await session.commit()
        root_id, second_id = root.id, second_turn.id

    # --- Re-run the campaign over the interrupted experiment ---
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

    # --- The campaign did NOT re-generate a round-6 root: the lineage grew by
    # exactly one turn, hanging off the SEEDED 2nd turn. ---
    async with AsyncSessionLocal() as session:
        attacks = list(
            (await session.execute(select(Attack).where(Attack.experiment_id == exp_id)))
            .scalars()
            .all()
        )
        assert len(attacks) == 3  # seeded root + seeded 2nd + new 3rd

        roots = [a for a in attacks if a.parent_attack_id is None]
        assert [a.id for a in roots] == [root_id]

        turn3 = [a for a in attacks if EVOLVED_MARKER in a.prompt_text]
        assert len(turn3) == 1
        turn3 = turn3[0]
        assert turn3.parent_attack_id == second_id

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
        assert vulns[0].attack_id == turn3.id
        assert vulns[0].verified_status == "CONFIRMED_VULNERABILITY"

    # --- The resumed run only touched the target once (turn 3), and that turn
    # carried the FULL 5-message conversation history. ---
    log = MockTargetProvider.observed_payloads
    assert len(log) == 3  # 2 seed executions + 1 campaign turn; nothing replayed

    seed_root, seed_second, turn3_exec = log
    assert len(seed_root["messages"]) == 1
    assert len(seed_second["messages"]) == 3
    assert seed_second["messages"][1]["content"] == root_res.response_text

    assert len(turn3_exec["messages"]) == 5
    assert [m["role"] for m in turn3_exec["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert turn3_exec["messages"][0]["content"] == _MULTI_TURN_ROOT
    assert turn3_exec["messages"][1]["content"] == root_res.response_text
    assert turn3_exec["messages"][2]["content"] == _MULTI_TURN_SECOND
    assert turn3_exec["messages"][3]["content"] == second_res.response_text
    assert turn3_exec["messages"][4]["content"] == turn3.prompt_text
    assert COMPLIANT_MARKER in turn3_exec["response_text"]
