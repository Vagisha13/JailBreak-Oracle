import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy.future import select
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment, TokenUsage


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


@pytest.mark.asyncio
async def test_campaign_status_includes_budget(api_client):
    """GET /campaigns/{id}/status must include token/cost summary (E-12 budget)."""
    from datetime import datetime, timezone

    # Seed a user + demo project/target via the real API.
    reg = await api_client.post(
        "/api/v1/auth/register",
        json={"email": f"status_{uuid.uuid4().hex[:8]}@oracle.sec", "password": "strongpass123"},
    )
    token = reg.json()["access_token"]
    headers = _auth_headers(token)
    demo = await api_client.post("/api/v1/campaigns/setup-demo", headers=headers)
    assert demo.status_code == 200, demo.text

    project_id = uuid.UUID(demo.json()["project_id"])
    target_id = uuid.UUID(demo.json()["target_id"])

    # Directly insert a COMPLETED experiment + two token_usage rows.
    async with AsyncSessionLocal() as session:
        exp = Experiment(
            project_id=project_id,
            target_id=target_id,
            name="Budget Check",
            status="COMPLETED",
            attack_budget=2,
            max_cost_usd=5.0,
        )
        session.add(exp)
        await session.flush()
        for role in ("attacker", "evaluator"):
            session.add(TokenUsage(
                experiment_id=exp.id,
                role=role,
                model="gpt-4o-mini",
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
                cost_usd=0.0015,
            ))
        await session.commit()
        experiment_id = str(exp.id)

    resp = await api_client.get(f"/api/v1/campaigns/{experiment_id}/status", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    budget = data["budget"]
    assert budget["prompt_tokens"] == 200
    assert budget["completion_tokens"] == 400
    assert budget["total_cost_usd"] == pytest.approx(0.003, abs=1e-4)
    assert budget["max_cost_usd"] == 5.0
