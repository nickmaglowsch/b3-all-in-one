"""A US backtest, through the real entry point the platform shells.

Everything else in this feature is unit-tested in pieces. This is the one that
fails if any seam between them does: the market-scoped loaders, the BRL
projection, the point-in-time universe built from a *claimed* listing interval
rather than an observed one, the delisting returns, the exterior's annual
apuração, the `$SPX` benchmark, and the coverage block the product reports its
gaps through.

Synthetic prices in a tmp_path SQLite — no network, no vendor.
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
from b3_pipeline import market_storage, storage, us_storage

ROOT = Path(__file__).resolve().parents[1]

SYMBOLS = ("AAA", "BBB", "CCC", "DEAD", "GONE")
# DEAD is the bankruptcy (worthless), GONE the ordinary venue delisting.
DELISTED = {"DEAD": ("2022-05-13", 1), "GONE": ("2023-06-01", 0)}
FX = 5.2

# The archive starts here. The backtest window below opens two years earlier,
# which is exactly the state #42 describes and the thing the product has to
# report rather than let a reader assume away.
COVERAGE_START = "2021-01-01"


@pytest.fixture(scope="module")
def us_plane(tmp_path_factory) -> str:
    db_path = str(tmp_path_factory.mktemp("plane") / "us.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)

    rng = np.random.default_rng(11)
    dates = pd.date_range(COVERAGE_START, "2023-12-31", freq="B")
    rows = []
    for sym in SYMBOLS:
        px = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, len(dates))))
        rows.extend(
            dict(market="US", symbol=sym, date=d.date(), open=p, high=p * 1.02,
                 low=p * 0.98, close=p, volume=5e6, adj_close=p,
                 split_adj_close=p, close_brl=p * FX, adj_close_brl=p * FX,
                 fx_rate=FX, source="alphavantage-time-series-daily:total_return")
            for d, p in zip(dates, px))
    market_storage.upsert_market_prices(conn, pd.DataFrame(rows))

    # Listing intervals as `us_listing` writes them: claimed, and reaching back
    # before the price archive does.
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="US", symbol=s, currency="USD", kind="EQUITY",
             listing_date="2015-01-01",
             delisting_date=DELISTED.get(s, (None, 0))[0],
             is_worthless=DELISTED.get(s, (None, 0))[1])
        for s in SYMBOLS]))
    us_storage.upsert_price_cursor(conn, pd.DataFrame([
        dict(symbol=s, first_bar=COVERAGE_START, last_bar="2023-12-31",
             adjust_state="total_return", status="ok") for s in SYMBOLS]))

    # $SPX, as spx_series writes it: a BRL total-return level in `close`.
    bench = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, len(dates))))
    storage.upsert_external_prices(conn, pd.DataFrame({
        "ticker": "$SPX", "isin_code": "UNKNOWN", "date": dates,
        "open": float("nan"), "high": float("nan"), "low": float("nan"),
        "close": bench * FX, "volume": 0.0, "source": "ALPHAVANTAGE_SPY_BRL"}))
    conn.commit()
    conn.close()
    return db_path


def _run(plane: str, tmp_path, start="2021-01-01", **params) -> dict:
    cfg = {"start": start, "end": "2023-12-31", "freq": "ME", "market": "US",
           "params": {"min_adtv": 1000.0, "min_price": 0.0, **params}}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         "BottomVol", str(tmp_path), json.dumps(cfg)],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "ENGINE_DATABASE_URL": plane,
             "ENGINE_BENCH_OFFLINE": "1"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert payload, proc.stdout[-2000:]
    return json.loads(payload[-1])


def test_a_us_backtest_runs_and_is_scored_in_reais(us_plane, tmp_path):
    m = _run(us_plane, tmp_path)
    assert m["market"] == "US"
    # BRL whatever the venue quoted. A US book therefore bundles the USD/BRL
    # move into its return, which is correct for this product's holder.
    assert m["currency"] == "BRL"
    assert m["periods"] > 24
    assert m["final_pretax_nav"] > 0


def test_the_dead_are_in_the_universe_and_then_out_of_it(us_plane):
    """The property the whole plane exists for, asserted where it is decidable.

    A name that died mid-window is tradable before it died and not after — not
    dropped from the start, which is the bias itself, and not forward-filled at
    a fixed price forever, which is a riskless fictional position. Read from the
    universe rather than from the runner's output, because the runner reports
    NAV and NAV cannot distinguish these.
    """
    from backtests.core import universe

    dates = pd.to_datetime(["2021-06-30", "2022-12-31", "2023-12-31"])
    prices = pd.DataFrame(1.0, index=dates, columns=list(SYMBOLS))
    tradable, delist_ret = universe.survivorship(us_plane, prices, "US")

    assert tradable.loc[dates[0], "DEAD"]       # alive in 2021
    assert not tradable.loc[dates[1], "DEAD"]   # gone by end-2022
    assert tradable.loc[dates[2], "AAA"]        # the survivors stay
    # And the write-off reaches the simulator as a return, so the loss lands in
    # the tax ledger like any other.
    assert delist_ret["DEAD"].min() == pytest.approx(-1.0)   # bankruptcy
    assert delist_ret["GONE"].min() == pytest.approx(0.0)    # venue delisting


def test_the_runner_reads_the_listing_intervals_it_was_given(us_plane, tmp_path):
    """Every instrument on this plane carries an interval, so the coverage block
    must not report the survivorship caveat. It is the same check inverted: a
    plane whose master had not been backfilled would say so here."""
    m = _run(us_plane, tmp_path)
    assert "listing_intervals_incomplete" not in {
        w["code"] for w in m["data_warnings"]}


def test_the_exterior_settles_once_a_year_not_at_every_rebalance(us_plane,
                                                                 tmp_path):
    """Lei 14.754/2023. Three calendar years in the window, so tax can land on
    at most three rows — a per-rebalance settlement would spread it over
    thirty-odd."""
    _run(us_plane, tmp_path)
    curves = pd.read_csv(tmp_path / "curves.csv")
    taxed_rows = int((curves["tax_paid"] > 0).sum())
    assert taxed_rows <= 3, (
        f"{taxed_rows} rows paid tax; the exterior nets the whole year and "
        "settles once, in the ajuste")


def test_a_window_that_opens_before_the_archive_says_so_in_its_own_result(
        us_plane, tmp_path):
    """#42's second checkbox. Before this the run returned a number that looked
    exactly as clean as one whose window was covered."""
    m = _run(us_plane, tmp_path, start="2019-01-01")
    codes = {w["code"] for w in m["data_warnings"]}
    assert "price_coverage_starts_late" in codes
    detail = next(w for w in m["data_warnings"]
                  if w["code"] == "price_coverage_starts_late")["detail"]
    assert detail["coverage_start"] == COVERAGE_START
    assert detail["requested_start"] == "2019-01-01"


def test_a_covered_window_carries_no_coverage_warning(us_plane, tmp_path):
    m = _run(us_plane, tmp_path, start="2021-06-01")
    assert "price_coverage_starts_late" not in {
        w["code"] for w in m["data_warnings"]}


def test_the_benchmark_reaches_the_bundle_the_strategies_read(us_plane,
                                                              monkeypatch):
    """`markets.MARKETS["US"].benchmark` has said `$SPX` since the plane was
    built and nothing wrote it, so `bench_ret` came back empty and a US run had
    no ruler. It never reaches metrics.json — it reaches the shared bundle — so
    that is where this asserts."""
    from b3_pipeline import config
    from backtests.core import shared_data

    monkeypatch.setattr(config, "DB_PATH", Path(us_plane))
    # The cash sleeve is CDI whatever the equity sleeve is, and BCB is not
    # reachable from a test. Stubbed narrowly so the benchmark path — the thing
    # under test — stays real.
    monkeypatch.setattr(shared_data, "download_cdi_daily", lambda s, e: pd.Series(
        0.0004, index=pd.date_range(s, e, freq="B")))
    shared = shared_data.build_shared_data(
        us_plane, "2021-01-01", "2023-12-31", "ME", market="US",
        include_fundamentals=False)
    assert not shared["ibov_ret"].empty
    # In BRL, like the strategy it is measuring — a bench without the same
    # conversion is the wrong ruler and the gap would read as skill.
    assert shared["ibov_px"].iloc[-1] > 100.0
