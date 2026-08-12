import pytest
import uuid
import json
from httpx import AsyncClient, ASGITransport
from sqlalchemy.future import select
from app.main import app
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
from app.agents.verifier import VerifierAgent
from app.services.verification import VerificationService
from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse


class MockVerifierProvider(TargetProvider):
    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        payload = {
            "is_confirmed": True,
            "reasoning": "Dual-verification confirmed exploitation.",
            "remediation_guidance": "Patch system prompt with structural boundaries.",
        }
        return TargetResponse(
            response_text=f"```json\n{json.dumps(payload)}\n```", latency_ms=10.0
        )


@pytest.mark.asyncio
async def test_verification_service_workflow():
    async with AsyncSessionLocal() as session:
        user = User(email=f"verify_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()

        project = Project(name="Verification Proj", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(
            project_id=project.id, name="Verify Target", provider_type="mock"
        )
        session.add(target)
        await session.commit()

        experiment = Experiment(
            project_id=project.id, target_id=target.id, name="Verify Exp"
        )
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

        result = AttackResult(
            attack_id=attack.id,
            target_response="Exfiltrated target response.",
            latency_ms=10.0,
        )
        session.add(result)
        await session.commit()

        vuln = Vulnerability(
            experiment_id=experiment.id,
            attack_id=attack.id,
            category="prompt_injection",
            severity="HIGH",
            confidence=0.88,
            reasoning="Initial evaluation flagged jailbreak.",
            verified_status="UNCONFIRMED",
        )
        session.add(vuln)
        await session.commit()
        vuln_id = vuln.id

    provider = MockVerifierProvider()
    verifier_agent = VerifierAgent(provider)
    service = VerificationService(verifier_agent)

    res = await service.verify_vulnerability(vuln_id)

    assert res.vulnerability_id == vuln_id
    assert res.verified_status == "CONFIRMED_VULNERABILITY"
    assert res.remediation_guidance == "Patch system prompt with structural boundaries."

    async with AsyncSessionLocal() as session:
        db_vuln = (
            (
                await session.execute(
                    select(Vulnerability).where(Vulnerability.id == vuln_id)
                )
            )
            .scalars()
            .first()
        )
        assert db_vuln.verified_status == "CONFIRMED_VULNERABILITY"


@pytest.mark.asyncio
async def test_vulnerability_api_endpoints():
    async with AsyncSessionLocal() as session:
        user = User(email=f"verify_api_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()

        project = Project(name="Verify API Proj", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(
            project_id=project.id, name="Verify API Target", provider_type="mock"
        )
        session.add(target)
        await session.commit()

        experiment = Experiment(
            project_id=project.id, target_id=target.id, name="Verify API Exp"
        )
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

        result = AttackResult(
            attack_id=attack.id, target_response="Target output", latency_ms=10.0
        )
        session.add(result)
        await session.commit()

        vuln = Vulnerability(
            experiment_id=experiment.id,
            attack_id=attack.id,
            category="prompt_injection",
            severity="HIGH",
            confidence=0.85,
            reasoning="Initial evaluation.",
            verified_status="UNCONFIRMED",
        )
        session.add(vuln)
        await session.commit()
        vuln_id = str(vuln.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_res = await client.get(f"/api/v1/vulnerabilities/{vuln_id}")
        assert get_res.status_code == 200
        assert get_res.json()["verified_status"] == "UNCONFIRMED"

        verify_res = await client.post(
            "/api/v1/vulnerabilities/verify", json={"vulnerability_id": vuln_id}
        )
        assert verify_res.status_code == 200
        assert verify_res.json()["verified_status"] == "CONFIRMED_VULNERABILITY"
