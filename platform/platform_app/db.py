import asyncio
from collections.abc import AsyncGenerator

import asyncpg
from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from platform_app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


# ── Engine market plane (raw asyncpg, ENGINE_DATABASE_URL) ────────────────────
# The market plane is read with plain SQL rather than the ORM, and every reader
# used to open its own connection and close it — a full TCP + auth handshake per
# call, five call sites, two of them on the same portfolio page load. One pool
# for the process instead.
#
# Lazily built rather than opened in a lifespan hook: the plane is optional (every
# reader already degrades gracefully when it is unreachable), so a pool created on
# first use keeps the API booting when the plane is down.
#
# Keyed by DSN, not a single global: a pool pins the DSN it was built with, so a
# lone global would keep serving the old database after settings.engine_database_url
# changed — which is exactly how the fundamentals guard is exercised (a test points
# the plane at an empty DB and expects the guard to fire).
_engine_pools: dict[str, asyncpg.Pool] = {}
_engine_pool_lock = asyncio.Lock()


async def get_engine_pool() -> asyncpg.Pool:
    """Shared connection pool for the engine market plane."""
    dsn = settings.engine_database_url
    pool = _engine_pools.get(dsn)
    if pool is None:
        async with _engine_pool_lock:
            # Re-check inside the lock: concurrent first callers must not each
            # build a pool and leak all but the last.
            pool = _engine_pools.get(dsn)
            if pool is None:
                pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
                _engine_pools[dsn] = pool
    return pool


async def close_engine_pool() -> None:
    """Close every market-plane pool. For tests that drop the database a pool is
    still holding connections to."""
    for pool in list(_engine_pools.values()):
        await pool.close()
    _engine_pools.clear()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    # imported here to avoid a models<->db import cycle
    from platform_app.models import OAuthAccount, User

    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)
