import pytest
import pytest_asyncio

from app.db.session import engine, AsyncSessionLocal
from app.db.base import Base


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    """
    Create all database tables before the test suite starts
    and drop them after the test suite finishes.
    """

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    """
    Provide a clean async database session for individual tests.
    """
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
