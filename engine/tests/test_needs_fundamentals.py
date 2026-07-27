"""needs_fundamentals flag + advise_weights' structured 'needs fundamentals' error.

Two layers:
  1. DB-independent: the code strategies that read fundamentals_pit report
     needs_fundamentals=True (pure introspection — safe anytime, even while the
     shared b3 DB is being ingested).
  2. Guard contract (Postgres only): against a throwaway EMPTY engine DB (prices
     seeded, fundamentals_pit left empty), advise_weights for a fundamentals-
     needing strategy exits with NEEDS_FUNDAMENTALS_EXIT (3) and a JSON error on
     stderr. Never touches b3's fundamentals tables (which move under us).
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import pytest

from b3_pipeline import storage

# Targets the Postgres plane: conftest's isolate_engine_dsn keeps
# ENGINE_DATABASE_URL in place for anything marked `postgres`.
pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "advise_weights.py"

# Code strategies that hard-depend on fundamentals_pit (directly or via a nested
# sleeve) and must therefore advertise the flag.
FUNDAMENTALS_STRATEGIES = [
    "Two-Leg Value",
    "BESST Quality",
    "Top Market Cap",
    "Value / CDI / IVVB11 (thirds)",
]


# ── 1. DB-independent flag introspection ──────────────────────────────────────

@pytest.mark.parametrize("name", FUNDAMENTALS_STRATEGIES)
def test_code_strategy_declares_needs_fundamentals(name):
    from backtests.core.strategy_registry import get_registry

    strat = get_registry().get(name)
    assert getattr(strat, "needs_fundamentals", False) is True, name


def test_price_only_strategy_does_not_need_fundamentals():
    # Control: a technical strategy must NOT be flagged (no false positives).
    from backtests.core.strategy_registry import get_registry

    strat = get_registry().get("MomSharpe")
    assert getattr(strat, "needs_fundamentals", False) is False


# ── 2. Guard contract against a throwaway empty Postgres DB ────────────────────

pg_only = pytest.mark.skipif(
    not storage.using_postgres(),
    reason="ENGINE_DATABASE_URL is not a postgresql:// DSN",
)


def _swap_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + dbname, parts.query, parts.fragment))


@contextlib.contextmanager
def _env(**overrides):
    old = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: v for k, v in overrides.items() if v is not None})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _seed_prices(conn) -> None:
    """A year of daily prices for a few tickers, adj columns = close. Enough for
    build_shared_data to produce a return plane; fundamentals_pit stays empty."""
    days = pd.bdate_range("2020-01-02", "2020-12-31")
    rows = []
    for tkr, base in (("PETR3", 30.0), ("VALE3", 60.0), ("ITUB4", 25.0)):
        for i, dt in enumerate(days):
            px = base + i * 0.01
            rows.append({
                "ticker": tkr, "isin_code": f"BR{tkr}ACNOR0",
                "date": dt.strftime("%Y-%m-%d"),
                "open": px, "high": px, "low": px, "close": px,
                "volume": 5_000_000, "quotation_factor": 1,
            })
    df = pd.DataFrame(rows)
    storage.upsert_prices(conn, df)
    for col in ("split_adj_open", "split_adj_high", "split_adj_low",
                "split_adj_close", "adj_close"):
        df[col] = df["close"]
    storage.update_adjusted_columns(conn, df)


@pytest.fixture()
def empty_fundamentals_db():
    """Throwaway `guard_test` DB: full schema + prices, fundamentals_pit empty."""
    base = os.environ["ENGINE_DATABASE_URL"]
    admin = _swap_db(base, "postgres")
    test_url = _swap_db(base, "guard_test")

    import psycopg
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS guard_test")
        c.execute("CREATE DATABASE guard_test")
    try:
        with _env(ENGINE_DATABASE_URL=test_url):
            conn = storage.get_connection()
            storage.init_db(conn, rebuild=True)
            _seed_prices(conn)
            assert int(storage.read_sql(
                "SELECT COUNT(*) AS n FROM fundamentals_pit", conn
            ).iloc[0]["n"]) == 0
            conn.close()
        yield test_url
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'guard_test' AND pid <> pg_backend_pid()"
            )
            c.execute("DROP DATABASE IF EXISTS guard_test")


@pg_only
def test_advise_emits_needs_fundamentals_error(empty_fundamentals_db):
    env = {
        **os.environ,
        "ENGINE_DATABASE_URL": empty_fundamentals_db,
        "ENGINE_BENCH_OFFLINE": "1",
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "Two-Leg Value"],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    assert proc.returncode == 3, (
        f"expected NEEDS_FUNDAMENTALS_EXIT=3, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    )
    # JSON error line on stderr, machine-detectable by the platform.
    payload = None
    for line in reversed(proc.stderr.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            break
    assert payload is not None, proc.stderr
    assert payload["error"] == "needs_fundamentals"
    assert payload["strategy"] == "Two-Leg Value"
    assert "fundamentals" in payload["message"].lower()
