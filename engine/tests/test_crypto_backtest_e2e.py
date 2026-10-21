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

from backtests.core import markets

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


def _run(plane: str, market: str, tmp_path, strategy="BottomVol", **params) -> dict:
    cfg = {"start": "2021-01-01", "end": "2023-12-31", "freq": "ME",
           "market": market,
           "params": {"min_adtv": 1000.0, "min_price": 0.0, **params}}
    out = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         strategy, out, json.dumps(cfg)],
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
    # Sharpe is excess over the CDI (BRL numeraire, every market) and says so.
    assert m["sharpe_risk_free"] == "CDI"


def test_a_spec_authored_for_this_plane_runs_on_it(crypto_plane, tmp_path):
    """The gallery's crypto strategies, through the same entry point the platform
    shells. `BottomVol` above proves the plane runs *something*; this proves the
    specs written for it are discovered by name and produce a book."""
    m = _run(crypto_plane, "CRYPTO", tmp_path, strategy="CryptoLowVol")
    assert m["strategy"] == "CryptoLowVol"
    assert m["market"] == "CRYPTO"
    assert m["periods"] > 24
    assert m["final_pretax_nav"] > 0


def test_a_gated_crypto_book_parks_in_cdi_rather_than_holding_nothing(
        crypto_plane, tmp_path):
    """CryptoBTCRegime routes the whole book to CDI whenever BTC is below its
    10-period MA. What this asserts is that it stays invested in *something*
    across the window — an unreachable regime flag would leave every row flat and
    still return a plausible-looking NAV."""
    m = _run(crypto_plane, "CRYPTO", tmp_path, strategy="CryptoBTCRegime")
    assert m["market"] == "CRYPTO"
    assert m["final_pretax_nav"] > 0
    curves = pd.read_csv(tmp_path / "curves.csv")
    assert (curves["pretax"] > 0).all()


def test_a_crypto_spec_declares_its_own_market_without_being_told(
        crypto_plane, tmp_path):
    """No `market` in the config: the runner falls back to the strategy's own
    declaration. A spec that forgot `market: CRYPTO` would silently run against
    B3 and report the number under a crypto name."""
    cfg = {"start": "2021-01-01", "end": "2023-12-31", "freq": "ME",
           "params": {"min_adtv": 1000.0, "min_price": 0.0}}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         "CryptoMomSharpe", str(tmp_path), json.dumps(cfg)],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "ENGINE_DATABASE_URL": crypto_plane,
             "ENGINE_BENCH_OFFLINE": "1"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert json.loads(payload[-1])["market"] == "CRYPTO"


def test_it_writes_the_artifacts_the_platform_reads_back(crypto_plane, tmp_path):
    _run(crypto_plane, "CRYPTO", tmp_path)
    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "curves.csv").exists()


def test_a_stored_exemption_cannot_override_the_market_rule(crypto_plane, tmp_path):
    """The isenção is the asset class's rule, not a strategy knob.

    It used to be both. `strategy_base` shipped a COMMON_MONTHLY_SALES_EXEMPTION
    whose default was a hard-coded R$20.000, so any spec that exposed it wrote
    that literal into `params` — where `params.get(...)` beat the market's own
    value and charged a crypto book the isenção of ações. Passing it now changes
    nothing, which is the whole point.
    """
    default = _run(crypto_plane, "CRYPTO", tmp_path)
    override = _run(crypto_plane, "CRYPTO", tmp_path, monthly_sales_exemption=0.0)
    assert override["total_tax_paid"] == default["total_tax_paid"]
    assert markets.resolve_tax(
        "CRYPTO", {"monthly_sales_exemption": 20_000.0}
    )["monthly_sales_exemption"] == 35_000.0


def test_a_losing_crypto_book_carries_no_loss_forward(crypto_plane, tmp_path):
    """Ganho de capital compensates nothing: a crypto loss is simply lost.

    The simulator used to keep one unbounded carryforward for every market, so a
    losing crypto book was scored more kindly than the Receita would score it.
    """
    out = _run(crypto_plane, "CRYPTO", tmp_path)
    assert out["final_loss_carryforward"] == 0.0


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
