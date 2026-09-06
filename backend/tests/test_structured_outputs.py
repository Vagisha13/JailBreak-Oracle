"""Phase 6 (E-09/E-10) — structured evaluator & verifier outputs.

Covers explicit verdict/disposition enums, constrained confidence/severity,
evaluator evidence, independent verifier input (no evaluator reasoning), and
persistence of verifier fields on the vulnerability record.
"""
import json
import uuid

import pytest
from sqlalchemy.future import select

from app.agents.evaluator import EvaluatorAgent
from app.agents.verifier import VerifierAgent
from app.db.session import AsyncSessionLocal
from app.models.domain import (
    User,
    Project,
    Target,
    Experiment,
    Attack,
    AttackResult,
    Vulnerability,
)
from app.schemas.evaluation import EvaluatorVerdict, VerdictType
from app.schemas.target import TargetResponse
from app.services.verification import VerificationService
from app.targets.base import TargetProvider


class RecordingProvider(TargetProvider):
    def __init__(self, payload: dict):
        self.payload = payload
        self.seen: list[str] = []

    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        self.seen.append(prompt)
        return TargetResponse(
            response_text=json.dumps(self.payload), latency_ms=1.0
        )


# --- Evaluator (E-10) ---


def test_evaluator_verdict_derived_from_flag():
    v = EvaluatorVerdict(
        is_jailbreak=True,
        severity="HIGH",
        category="prompt_injection",
        confidence=0.9,
        reasoning="compiled",
    )
    assert v.verdict == VerdictType.JAILBREAK
    assert v.is_jailbreak is True


def test_evaluator_explicit_ambiguous_verdict():
    v = EvaluatorVerdict(
        verdict=VerdictType.AMBIGUOUS,
        severity="LOW",
        category="prompt_injection",
        confidence=0.5,
        reasoning="evasive non-answer",
    )
    assert v.verdict == VerdictType.AMBIGUOUS
    assert v.is_jailbreak is False


def test_evaluator_constraints_and_evidence():
    with pytest.raises(Exception):
        EvaluatorVerdict(
            is_jailbreak=True,
            severity="MEGA",
            category="x",
            confidence=0.9,
            reasoning="r",
        )
    with pytest.raises(Exception):
        EvaluatorVerdict(
            is_jailbreak=True,
            severity="HIGH",
            category="x",
            confidence=1.5,
            reasoning="r",
        )
    v = EvaluatorVerdict(
        is_jailbreak=True,
        severity="HIGH",
        category="x",
        confidence=0.8,
        reasoning="r",
        evidence=["target: 'here is the data'"],
        false_positive_indicators=["model roleplayed the response"],
    )
    assert v.evidence == ["target: 'here is the data'"]
    assert v.false_positive_indicators == ["model roleplayed the response"]


@pytest.mark.asyncio
async def test_evaluator_agent_requests_structured_schema():
    provider = RecordingProvider(
        {
            "verdict": "BLOCKED",
            "severity": "NONE",
            "category": "blocked",
            "confidence": 0.99,
            "reasoning": "refused",
            "evidence": [],
            "false_positive_indicators": [],
        }
    )
    agent = EvaluatorAgent(provider=provider)
    verdict = await agent.evaluate("obj", "prompt", "I cannot help with that.")
    assert verdict.verdict == VerdictType.BLOCKED
    assert verdict.is_jailbreak is False
    assert "false_positive_indicators" in provider.seen[0]
    assert "AMBIGUOUS" in provider.seen[0]


# --- Verifier (E-09) ---


@pytest.mark.asyncio
async def test_verifier_is_independent_of_evaluator_reasoning():
    provider = RecordingProvider(
        {
            "disposition": "CONFIRMED",
            "reasoning": "genuine compliance",
            "evidence": ["data leaked"],
            "confidence": 0.95,
            "remediation_guidance": "patch prompt boundary",
        }
    )
    agent = VerifierAgent(provider=provider)
    verdict = await agent.verify(attack_prompt="bypass", target_response="here it is")
    assert verdict.disposition.value == "CONFIRMED"
    assert verdict.is_confirmed is True
    assert "INITIAL EVALUATOR REASONING" not in provider.seen[0]
    assert "evaluator" not in provider.seen[0].lower().split()[-3:]


def test_verifier_disposition_mapping():
    from app.agents.verifier import VerificationDisposition
    from app.schemas.verification import VerificationResult
    from app.services.verification import _STATUS_BY_DISPOSITION

    assert _STATUS_BY_DISPOSITION[VerificationDisposition.CONFIRMED] == (
        "CONFIRMED_VULNERABILITY"
    )
    assert _STATUS_BY_DISPOSITION[VerificationDisposition.REFUTED] == "FALSE_POSITIVE"
    assert _STATUS_BY_DISPOSITION[VerificationDisposition.INCONCLUSIVE] == "INCONCLUSIVE"
    # result schema stays compatible
    res = VerificationResult(
        vulnerability_id=uuid.uuid4(),
        verified_status="INCONCLUSIVE",
        verification_reasoning="cannot determine",
    )
    assert res.verification_reasoning == "cannot determine"


async def _seed_vuln(inconclusive: bool = False) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = User(email=f"struct_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()
        project = Project(name="Struct", owner_id=user.id)
        session.add(project)
        await session.commit()
        target = Target(project_id=project.id, name="T", provider_type="mock")
        session.add(target)
        await session.commit()
        experiment = Experiment(project_id=project.id, target_id=target.id, name="E")
        session.add(experiment)
        await session.commit()
        attack = Attack(
            experiment_id=experiment.id,
            strategy_name="test",
            category="test",
            prompt_text="bypass prompt",
        )
        session.add(attack)
        await session.commit()
        session.add(
            AttackResult(attack_id=attack.id, target_response="raw target output")
        )
        vuln = Vulnerability(
            experiment_id=experiment.id,
            attack_id=attack.id,
            category="prompt_injection",
            severity="HIGH",
            confidence=0.9,
            reasoning="evaluator said jailbreak",
            verified_status="UNCONFIRMED",
        )
        session.add(vuln)
        await session.commit()
        return vuln.id


@pytest.mark.asyncio
async def test_verifier_fields_persisted_and_confirmed():
    vuln_id = await _seed_vuln()
    provider = RecordingProvider(
        {
            "disposition": "CONFIRMED",
            "reasoning": "confirmed exploitation",
            "evidence": ["data"],
            "confidence": 0.97,
            "remediation_guidance": "harden system prompt",
        }
    )
    service = VerificationService(VerifierAgent(provider=provider))
    result = await service.verify_vulnerability(vuln_id)

    assert result.verified_status == "CONFIRMED_VULNERABILITY"
    assert result.remediation_guidance == "harden system prompt"

    async with AsyncSessionLocal() as session:
        vuln = (
            (
                await session.execute(
                    select(Vulnerability).where(Vulnerability.id == vuln_id)
                )
            )
            .scalars()
            .first()
        )
        assert vuln.verified_status == "CONFIRMED_VULNERABILITY"
        assert vuln.verification_reasoning == "confirmed exploitation"
        assert vuln.remediation_guidance == "harden system prompt"
        assert vuln.verifier_confidence == 0.97
        assert vuln.verified_at is not None
        # evaluator reasoning is preserved untouched (independent fields).
        assert vuln.reasoning == "evaluator said jailbreak"


@pytest.mark.asyncio
async def test_verifier_inconclusive_persisted():
    vuln_id = await _seed_vuln()
    provider = RecordingProvider(
        {
            "disposition": "INCONCLUSIVE",
            "reasoning": "cannot tell",
            "confidence": 0.4,
        }
    )
    service = VerificationService(VerifierAgent(provider=provider))
    result = await service.verify_vulnerability(vuln_id)
    assert result.verified_status == "INCONCLUSIVE"

    async with AsyncSessionLocal() as session:
        vuln = (
            await session.execute(select(Vulnerability).where(Vulnerability.id == vuln_id))
        ).scalars().first()
        assert vuln.verified_status == "INCONCLUSIVE"
        assert vuln.verification_reasoning == "cannot tell"
