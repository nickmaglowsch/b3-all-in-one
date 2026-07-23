import os

# Ephemeral test DB name is overridable so parallel test runs (e.g. two feature
# agents) don't race on the same database. Set PLATFORM_TEST_DB to isolate.
TEST_DB = os.environ.get("PLATFORM_TEST_DB", "platform_test")

# Point the app at the test DB before any platform_app import reads settings.
os.environ["DATABASE_URL"] = (
    f"postgresql+asyncpg://postgres:devpassword@localhost:5432/{TEST_DB}"
)
os.environ["ENVIRONMENT"] = "development"
# Backtests read the market plane from the real `b3` DB (engine_database_url's
# default). Stub benchmarks so the engine subprocess never touches yfinance/BCB.
os.environ["ENGINE_BENCH_OFFLINE"] = "1"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_DSN = "postgresql+asyncpg://postgres:devpassword@localhost:5432/postgres"


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

    # Procrastinate ships its own tables; the worker applies them in prod. Tests never
    # run the worker but POST /imports defers a job, so apply the schema here.
    from platform_app.tasks import app as pro_app

    async with pro_app.open_async():
        row = await pro_app.connector.execute_query_one_async(
            "SELECT to_regclass('procrastinate_jobs') AS reg"
        )
        if row["reg"] is None:
            await pro_app.schema_manager.apply_schema_async()
    yield
    await engine.dispose()
    await _admin(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    from platform_app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
