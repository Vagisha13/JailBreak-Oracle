import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy.future import select
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_campaign_api_flow(api_client, mock_llm_providers):
    # 1. Register a user through the real auth flow
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={"email": f"api_{uuid.uuid4()}@oracle.sec", "password": "strongpass123"},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    headers = _auth_headers(token)

    # 2. Create a demo project + target owned by this user
    demo = await api_client.post("/api/v1/campaigns/setup-demo", headers=headers)
    assert demo.status_code == 200, demo.text
    project_id = demo.json()["project_id"]
    target_id = demo.json()["target_id"]

    # 3. Trigger Campaign Launch
    payload = {
        "name": "API Test Campaign",
        "project_id": project_id,
        "target_id": target_id,
        "attack_budget": 1,
        "exploration_ratio": 0.0,
    }

    response = await api_client.post("/api/v1/campaigns/start", json=payload, headers=headers)

    # The start endpoint creates a new experiment internally, enqueues it,
    # and returns quickly with the queued PENDING status.
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PENDING"
    experiment_id = uuid.UUID(data["experiment_id"])

    async with AsyncSessionLocal() as session:
        exp = (
            (
                await session.execute(select(Experiment).where(Experiment.id == experiment_id))
            )
            .scalars()
            .first()
        )
        assert exp is not None
        assert exp.status in ("PENDING", "RUNNING", "COMPLETED", "FAILED")
