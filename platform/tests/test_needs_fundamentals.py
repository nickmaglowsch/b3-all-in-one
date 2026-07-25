"""Fundamentals guard: /portfolio/target and /portfolio/rebalance-plans return a
clean 422 (never a 500) when a sleeve needs CVM fundamentals that aren't ingested,
and PUT /portfolio/sleeves saves with a warning instead of hard-refusing.

Deterministic and independent of b3's live state: it stands up a throwaway EMPTY
engine DB (schema + a price plane, fundamentals_pit left empty) and points the
platform at it via settings.engine_database_url.
"""
from __future__ import annotations

import os
import subprocess
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import delete

from platform_app import enginerun, routes
from platform_app.config import settings
from platform_app.db import async_session_maker, close_engine_pool
from platform_app.models import PortfolioSleeve

pytestmark = pytest.mark.asyncio(loop_scope="session")

STRAT = "Two-Leg Value"  # a code strategy that reads fundamentals_pit

# Built inside the engine venv (the platform venv has no pandas/backtests): full
# engine schema + a year of daily prices, with fundamentals_pit left empty.
# ponytail: inline bootstrap for one throwaway DB — not worth a shared helper.
_BOOTSTRAP = r"""
import pandas as pd
from b3_pipeline import storage
conn = storage.get_connection()
storage.init_db(conn, rebuild=True)
days = pd.bdate_range("2020-01-02", "2020-12-31")
rows = []
for tkr, base in (("PETR3", 30.0), ("VALE3", 60.0), ("ITUB4", 25.0)):
    for i, dt in enumerate(days):
        px = base + i * 0.01
        rows.append(dict(ticker=tkr, isin_code="BR" + tkr + "ACNOR0",
                         date=dt.strftime("%Y-%m-%d"), open=px, high=px, low=px,
                         close=px, volume=5_000_000, quotation_factor=1))
df = pd.DataFrame(rows)
storage.upsert_prices(conn, df)
for c in ("split_adj_open", "split_adj_high", "split_adj_low", "split_adj_close", "adj_close"):
    df[c] = df["close"]
storage.update_adjusted_columns(conn, df)
conn.close()
"""


def _swap_db(url: str, dbname: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + dbname, p.query, p.fragment))


@pytest_asyncio.fixture(loop_scope="session")
async def empty_engine_db():
    base = settings.engine_database_url
    admin = _swap_db(base, "postgres")
    test_dsn = _swap_db(base, "guard_engine")

    conn = await asyncpg.connect(admin)
    try:
        await conn.execute("DROP DATABASE IF EXISTS guard_engine WITH (FORCE)")
        await conn.execute("CREATE DATABASE guard_engine")
    finally:
        await conn.close()

    proc = subprocess.run(
        [*enginerun._engine_python(), "-c", _BOOTSTRAP],
        cwd=enginerun.ENGINE,
        env={**os.environ, "ENGINE_DATABASE_URL": test_dsn, "PYTHONWARNINGS": "ignore"},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr

    old = settings.engine_database_url
    settings.engine_database_url = test_dsn
    routes._SLEEVE_WEIGHTS_CACHE.clear()
    try:
        yield test_dsn
    finally:
        settings.engine_database_url = old
        routes._SLEEVE_WEIGHTS_CACHE.clear()
        # The market-plane pool holds live connections to guard_engine; close it
        # before dropping the database out from under it. Same reason the sleeve
        # cache above is cleared — this fixture mutates global config, so it owns
        # resetting the global state derived from it.
        await close_engine_pool()
        conn = await asyncpg.connect(admin)
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname='guard_engine' AND pid <> pg_backend_pid()")
            await conn.execute("DROP DATABASE IF EXISTS guard_engine WITH (FORCE)")
        finally:
            await conn.close()


async def _clear_sleeves() -> None:
    async with async_session_maker() as s:
        await s.execute(delete(PortfolioSleeve))
        await s.commit()


async def test_guard_422_without_fundamentals(client, empty_engine_db):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # PUT saves (no hard-refuse) but warns that fundamentals are missing.
        r = await client.put(
            "/portfolio/sleeves",
            json=[{"strategy_ref": STRAT, "target_pct": 100}], headers=headers)
        assert r.status_code == 200, r.text
        warnings = r.json()["warnings"]
        assert warnings and STRAT in warnings[0] and "fundamentalistas" in warnings[0]

        # GET /target → clean 422 naming the strategy (not a 500).
        rt = await client.get("/portfolio/target", headers=headers)
        assert rt.status_code == 422, rt.text
        detail = rt.json()["detail"]
        assert STRAT in detail and "fundamentalistas" in detail

        # POST /rebalance-plans → same 422.
        rp = await client.post(
            "/portfolio/rebalance-plans", json={"new_cash": 0}, headers=headers)
        assert rp.status_code == 422, rp.text
        assert STRAT in rp.json()["detail"]
    finally:
        await _clear_sleeves()
        routes._SLEEVE_WEIGHTS_CACHE.clear()
