import pytest
import uuid
import asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.domain import User, Project, Target, Experiment


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_campaign_api_flow():
    # 1. Setup DB Context for API test
    async with AsyncSessionLocal() as session:
        user = User(email=f"api_{uuid.uuid4()}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.commit()

        project = Project(name="API Test Project", owner_id=user.id)
        session.add(project)
        await session.commit()

        target = Target(project_id=project.id, name="API Target", provider_type="mock")
        session.add(target)
        await session.commit()

        experiment = Experiment(
            project_id=project.id, target_id=target.id, name="API Campaign"
        )
        session.add(experiment)
        await session.commit()

        exp_id = str(experiment.id)
        target_id = str(target.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Trigger Campaign Launch
        payload = {
            "experiment_id": exp_id,
            "target_id": target_id,
            "objective": "Bypass safety filters via API",
            "max_rounds": 1,
            "stop_on_first_success": True,
        }

        response = await client.post("/api/v1/campaigns/start", json=payload)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "PENDING"
        assert data["experiment_id"] == exp_id

        # 3. Check Status Endpoint (Should immediately be PENDING)
        status_resp = await client.get(f"/api/v1/campaigns/{exp_id}/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "PENDING"

        # 4. Wait a tiny bit for the BackgroundTask to complete the mock execution
        await asyncio.sleep(0.5)

        # 5. Check Status Endpoint again (Should now be COMPLETED)
        final_status_resp = await client.get(f"/api/v1/campaigns/{exp_id}/status")
        assert final_status_resp.status_code == 200
        assert final_status_resp.json()["status"] == "COMPLETED"
