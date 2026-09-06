import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


def _derive_sync_url(async_url: str) -> str:
    """Derive the synchronous driver URL used by Alembic from the async URL."""
    if async_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + async_url[
            len("postgresql+asyncpg://"):
        ]
    if async_url.startswith("sqlite+aiosqlite://"):
        return "sqlite://" + async_url[len("sqlite+aiosqlite://"):]
    return async_url


# ---------------------------------------------------------------------------
# Isolated test database.
#
# Tests MUST NEVER touch the development/production database. Before any
# application module is imported we redirect the engine to a dedicated temp
# SQLite file (or to TEST_DATABASE_URL when the operator provides one, e.g. a
# dedicated PostgreSQL test instance in CI).
# ---------------------------------------------------------------------------
_test_db_dir: Path | None = None
_test_async_url = os.environ.get("TEST_DATABASE_URL")
if not _test_async_url:
    _test_db_dir = Path(tempfile.mkdtemp(prefix="oracle_tests_"))
    _test_async_url = f"sqlite+aiosqlite:///{_test_db_dir / 'test_oracle.db'}"

os.environ["ASYNC_DATABASE_URL"] = _test_async_url
os.environ["SYNC_DATABASE_URL"] = _derive_sync_url(_test_async_url)

from app.main import app  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.ratelimit import reset_rate_limiter  # noqa: E402
from app.db.session import engine, AsyncSessionLocal  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    """
    Create all database tables for the isolated test database before the suite
    runs and drop them afterwards. The production/dev database is never touched.
    """
    # Rate limiting is disabled for the test suite by default so that unrelated
    # tests never trip shared global limits. Rate-limit tests re-enable it
    # explicitly and reset limiter state between checks.
    original_enabled = settings.RATE_LIMIT_ENABLED
    settings.RATE_LIMIT_ENABLED = False
    reset_rate_limiter()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    settings.RATE_LIMIT_ENABLED = original_enabled

    if _test_db_dir is not None:
        shutil.rmtree(_test_db_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def db_session():
    """
    Provide a clean async database session for individual tests.
    """
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def api_client():
    """Provide an AsyncClient for the canonical FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _create_user_and_token(
    client: AsyncClient, email: str | None = None, password: str = "strongpass123"
) -> dict:
    """Register a user through the real API and return credentials + JWT."""
    email = email or f"user_{uuid.uuid4().hex[:12]}@oracle.sec"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return {
        "email": email,
        "password": password,
        "token": resp.json()["access_token"],
    }


@pytest_asyncio.fixture
async def register_user(api_client):
    """Fixture returning a function that registers a user via the real API."""

    async def _register(email: str | None = None, password: str = "strongpass123"):
        return await _create_user_and_token(api_client, email=email, password=password)

    return _register


@pytest.fixture
def mock_llm_providers(monkeypatch):
    """Point all LLM providers at the deterministic mock so tests never hit the network."""
    for attr in (
        "ATTACKER_PROVIDER",
        "EVALUATOR_PROVIDER",
        "VERIFIER_PROVIDER",
        "DEFAULT_TARGET_PROVIDER",
    ):
        monkeypatch.setattr(settings, attr, "mock")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")
    return None


@pytest_asyncio.fixture
async def demo_env(api_client, register_user):
    """Register a user and create a demo project/target owned by them."""

    async def _create(token: str | None = None) -> dict:
        creds = {"token": token} if token else await register_user()
        resp = await api_client.post(
            "/api/v1/campaigns/setup-demo",
            headers={"Authorization": f"Bearer {creds['token']}"},
        )
        assert resp.status_code == 200, resp.text
        return {
            "token": creds["token"],
            "email": creds["email"],
            **resp.json(),
        }

    return _create


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
