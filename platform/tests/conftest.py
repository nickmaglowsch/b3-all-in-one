import os

# Point the app at an ephemeral test DB before any platform_app import reads settings.
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres:devpassword@localhost:5432/platform_test"
)
os.environ["ENVIRONMENT"] = "development"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_DSN = "postgresql+asyncpg://postgres:devpassword@localhost:5432/postgres"
TEST_DB = "platform_test"


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_DSN, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _test_db():
    await _admin(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await _admin(f"CREATE DATABASE {TEST_DB}")

    from platform_app.db import Base, engine
    import platform_app.models  # noqa: F401  register tables

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    await _admin(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    from platform_app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
