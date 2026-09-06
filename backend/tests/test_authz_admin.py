"""Role-based authorization tests.

Covers the admin bootstrap-on-register, the admin-only user directory + role
assignment endpoints, self-demotion protection, and the researcher 403 path.

The test database is shared for the whole suite, so every response makes its own
unique email addresses.
"""
import uuid

import pytest

from app.core.config import settings
from tests.conftest import _auth


def _email(prefix: str = "user") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}@oracle.sec"


async def _user_id(api_client, admin_token: str, email: str) -> str:
    resp = await api_client.get("/api/v1/auth/users", headers=_auth(admin_token))
    assert resp.status_code == 200
    for entry in resp.json():
        if entry["email"] == email:
            return entry["id"]
    raise AssertionError(f"{email} not in user directory")


@pytest.mark.asyncio
async def test_bootstrap_admin_email_promoted_on_register(api_client, monkeypatch):
    email = _email("admin")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", f"  {email} ")

    resp = await api_client.post(
        "/api/v1/auth/register", json={"email": email.upper(), "password": "strongpass123"}
    )
    assert resp.status_code == 201, resp.text

    me = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_default_registration_is_researcher(api_client):
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={"email": _email(), "password": "strongpass123"},
    )
    assert resp.status_code == 201, resp.text

    me = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["role"] == "researcher"


@pytest.mark.asyncio
async def test_admin_users_directory_lists_all_users(api_client, register_user, monkeypatch):
    boss_email = _email("boss")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", boss_email)
    admin = await register_user(boss_email)
    peer1 = _email("peer1")
    peer2 = _email("peer2")
    await register_user(peer1)
    await register_user(peer2)

    resp = await api_client.get("/api/v1/auth/users", headers=_auth(admin["token"]))
    assert resp.status_code == 200, resp.text
    by_email = {u["email"]: u for u in resp.json()}
    assert by_email[boss_email]["role"] == "admin"
    assert by_email[peer1]["role"] == "researcher"
    assert by_email[peer2]["role"] == "researcher"


@pytest.mark.asyncio
async def test_users_directory_requires_authentication(api_client):
    resp = await api_client.get("/api/v1/auth/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_users_directory_forbidden_for_researcher(api_client, register_user):
    creds = await register_user(_email("researcher"))
    resp = await api_client.get("/api/v1/auth/users", headers=_auth(creds["token"]))
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


@pytest.mark.asyncio
async def test_admin_can_promote_researcher(api_client, register_user, monkeypatch):
    boss_email = _email("boss")
    peer_email = _email("peer")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", boss_email)
    admin = await register_user(boss_email)
    peer = await register_user(peer_email)

    resp = await api_client.patch(
        f"/api/v1/auth/users/{await _user_id(api_client, admin['token'], peer_email)}/role",
        headers=_auth(admin["token"]),
        json={"role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"

    me = await api_client.get("/api/v1/auth/me", headers=_auth(peer["token"]))
    assert me.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_role_change_forbidden_for_researcher(api_client, register_user, monkeypatch):
    boss_email = _email("boss")
    peer_email = _email("peer")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", boss_email)
    admin = await register_user(boss_email)
    peer = await register_user(peer_email)

    resp = await api_client.patch(
        f"/api/v1/auth/users/{await _user_id(api_client, admin['token'], peer_email)}/role",
        headers=_auth(peer["token"]),
        json={"role": "admin"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


@pytest.mark.asyncio
async def test_admin_cannot_demote_self(api_client, register_user, monkeypatch):
    boss_email = _email("boss")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", boss_email)
    admin = await register_user(boss_email)

    resp = await api_client.patch(
        f"/api/v1/auth/users/{await _user_id(api_client, admin['token'], boss_email)}/role",
        headers=_auth(admin["token"]),
        json={"role": "researcher"},
    )
    assert resp.status_code == 400

    me = await api_client.get("/api/v1/auth/me", headers=_auth(admin["token"]))
    assert me.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_invalid_role_rejected(api_client, register_user, monkeypatch):
    boss_email = _email("boss")
    peer_email = _email("peer")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", boss_email)
    admin = await register_user(boss_email)
    await register_user(peer_email)

    resp = await api_client.patch(
        f"/api/v1/auth/users/{await _user_id(api_client, admin['token'], peer_email)}/role",
        headers=_auth(admin["token"]),
        json={"role": "supreme-leader"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_role_change_unknown_user_404(api_client, register_user, monkeypatch):
    boss_email = _email("boss")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAILS", boss_email)
    admin = await register_user(boss_email)

    resp = await api_client.patch(
        f"/api/v1/auth/users/{uuid.uuid4()}/role",
        headers=_auth(admin["token"]),
        json={"role": "researcher"},
    )
    assert resp.status_code == 404
