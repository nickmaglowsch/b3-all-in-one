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

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_DSN = "postgresql+asyncpg://postgres:devpassword@localhost:5432/postgres"


def _market_plane_loaded() -> bool:
    """True when the engine's `prices` table exists and has rows. Tests marked
    `market` shell into the engine over the real b3 plane; on a box without B3
    history (CI — the data is kept out of git for privacy) they can't pass, so
    they skip instead of failing. Load the plane locally and they run."""
    import psycopg

    dsn = os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://postgres:devpassword@localhost:5432/b3"
    )
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            if conn.execute("SELECT to_regclass('prices')").fetchone()[0] is None:
                return False
            return conn.execute("SELECT EXISTS (SELECT 1 FROM prices)").fetchone()[0]
    except psycopg.Error:
        return False


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "market: needs a loaded B3 market plane (skipped when empty)"
    )


def pytest_collection_modifyitems(config, items):
    if _market_plane_loaded():
        return
    skip = pytest.mark.skip(reason="market plane empty — load b3 prices to run")
    for item in items:
        if "market" in item.keywords:
            item.add_marker(skip)


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
