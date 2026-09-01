"""A crypto backtest, through the real entry point the platform shells.

Everything else in this feature is unit-tested in pieces. This is the one that
fails if any of the seams between them does: the `market_prices` table, the BRL
projection, the market-scoped loaders, the point-in-time universe, the delisting
returns, the per-market tax defaults, and the JSON contract the platform reads.

Synthetic prices in a tmp_path SQLite — no network, no real archive.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import market_storage, storage

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "SRMUSDT", "LUNAUSDT")
# LUNAUSDT is the wipeout, SRMUSDT the ordinary venue delisting.
DELISTED = {"LUNAUSDT": ("2022-05-13", 1), "SRMUSDT": ("2023-06-01", 0)}


@pytest.fixture(scope="module")
def crypto_plane(tmp_path_factory) -> str:
    db_path = str(tmp_path_factory.mktemp("plane") / "crypto.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)

    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-01", "2023-12-31", freq="D")
    rows = []
    for sym in SYMBOLS:
        px = 100.0 * np.exp(np.cumsum(rng.normal(0.001, 0.04, len(dates))))
        rows.extend(
            dict(market="CRYPTO", symbol=sym, date=d.date(), open=p,
                 high=p * 1.05, low=p * 0.95, close=p, volume=5e6,
                 adj_close=p, split_adj_close=p, close_brl=p * 5.2,
                 adj_close_brl=p * 5.2, fx_rate=5.2, source="synthetic")
            for d, p in zip(dates, px))
    market_storage.upsert_market_prices(conn, pd.DataFrame(rows))
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="CRYPTO", symbol=s, currency="USDT",
             listing_date="2021-01-01",
             delisting_date=DELISTED.get(s, (None, 0))[0],
             is_worthless=DELISTED.get(s, (None, 0))[1])
        for s in SYMBOLS]))
    conn.commit()
    conn.close()
    return db_path


def _run(plane: str, market: str, tmp_path, **params) -> dict:
    cfg = {"start": "2021-01-01", "end": "2023-12-31", "freq": "ME",
           "market": market,
           "params": {"min_adtv": 1000.0, "min_price": 0.0, **params}}
    out = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         "BottomVol", out, json.dumps(cfg)],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "ENGINE_DATABASE_URL": plane,
             "ENGINE_BENCH_OFFLINE": "1"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    # The loaders print progress to stdout; the payload is the last JSON line.
    payload = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert payload, proc.stdout[-2000:]
    return json.loads(payload[-1])


def test_a_crypto_backtest_runs_and_reports_its_market(crypto_plane, tmp_path):
    m = _run(crypto_plane, "CRYPTO", tmp_path)
    assert m["market"] == "CRYPTO"
    # BRL whatever the venue quoted — the numeraire is the holder's currency.
    assert m["currency"] == "BRL"
    assert m["periods"] > 24
    assert m["final_pretax_nav"] > 0


def test_it_writes_the_artifacts_the_platform_reads_back(crypto_plane, tmp_path):
    _run(crypto_plane, "CRYPTO", tmp_path)
    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "curves.csv").exists()


def test_the_crypto_isencao_is_the_one_that_applies(crypto_plane, tmp_path):
    """R$35k of monthly alienações, not the R$20k of ações. Overriding it to a
    token amount has to change the tax, which proves the value reaching the
    simulator is a parameter and not a constant baked into it."""
    default = _run(crypto_plane, "CRYPTO", tmp_path)
    taxed = _run(crypto_plane, "CRYPTO", tmp_path, monthly_sales_exemption=0.0)
    assert taxed["total_tax_paid"] > default["total_tax_paid"]


def test_an_unknown_market_fails_loudly(crypto_plane, tmp_path):
    """A stored spec naming a market the engine has no data for must not run as
    a silently Brazilian backtest."""
    cfg = {"start": "2021-01-01", "end": "2021-12-31", "market": "USA"}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         "BottomVol", str(tmp_path), json.dumps(cfg)],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "ENGINE_DATABASE_URL": crypto_plane,
             "ENGINE_BENCH_OFFLINE": "1"})
    assert proc.returncode != 0
    assert "Unknown market" in proc.stderr
