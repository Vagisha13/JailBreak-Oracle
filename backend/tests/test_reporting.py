import pytest
import uuid
from httpx import AsyncClient, ASGITransport
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
from app.services.report import ReportService


@pytest.mark.asyncio
async def test_report_service_calculation():
    # 1. Setup Test Data
    async with AsyncSessionLocal() as session:
        user = User(email=f"report_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()

        project = Project(name="Report Project", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(
            project_id=project.id, name="Report Target", provider_type="mock"
        )
        session.add(target)
        await session.commit()

        exp = Experiment(
            project_id=project.id, target_id=target.id, name="Report Campaign"
        )
        session.add(exp)
        await session.commit()

        attack1 = Attack(
            experiment_id=exp.id,
            strategy_name="tree_of_thought",
            category="jailbreak",
            prompt_text="prompt 1",
        )
        attack2 = Attack(
            experiment_id=exp.id,
            strategy_name="tree_of_thought",
            category="jailbreak",
            prompt_text="prompt 2",
        )
        session.add_all([attack1, attack2])
        await session.commit()

        vuln = Vulnerability(
            experiment_id=exp.id,
            attack_id=attack1.id,
            category="jailbreak",
            severity="HIGH",
            confidence=0.9,
            reasoning="Jailbreak succeeded.",
            verified_status="CONFIRMED_VULNERABILITY",
        )
        session.add(vuln)
        await session.commit()
        exp_id = exp.id

    # 2. Run Service
    service = ReportService()
    report = await service.generate_experiment_report(exp_id)

    # 3. Assert Metrics
    assert report.experiment_id == exp_id
    assert report.total_attacks_executed == 2
    assert report.jailbreak_success_rate == 50.0
    assert report.severity_breakdown.high == 1
    assert report.severity_breakdown.critical == 0
    assert report.overall_risk_score == 15.0
    assert len(report.strategy_performance) == 1
    assert report.strategy_performance[0].strategy_name == "tree_of_thought"
    assert report.strategy_performance[0].successful_jailbreaks == 1


@pytest.mark.asyncio
async def test_report_api_endpoint():
    async with AsyncSessionLocal() as session:
        user = User(email=f"report_api_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()

        project = Project(name="Report API Proj", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(
            project_id=project.id, name="Report API Target", provider_type="mock"
        )
        session.add(target)
        await session.commit()

        exp = Experiment(
            project_id=project.id, target_id=target.id, name="Report API Exp"
        )
        session.add(exp)
        await session.commit()
        exp_id = str(exp.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/api/v1/reports/experiment/{exp_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["experiment_id"] == exp_id
        assert "overall_risk_score" in data
        assert "severity_breakdown" in data
