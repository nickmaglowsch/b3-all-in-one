"""The US benchmark, when its stored series is missing.

`$SPX` is SPY's total return in reais, written by us_pipeline/spx_series.py — a
job that needs AlphaVantage. When that job fails, nothing writes `$SPX`, and
before this fallback every bench_* regime flag on US read False: a trend-gated
US book (the built-in USTrendRegime included) held 100% CDI over every window
with no warning, and read as a calm, cash-like strategy.

Now the market's own benchmark falls back to its proxy on the plane
(`MarketSpec.benchmark_proxy`, SPY), and a run says which ruler it used — or
that it had none.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage
from backtests.core import markets
from backtests.core.data import download_benchmark
from backtests.core.shared_data import _benchmark_notes, build_shared_data

DAYS = pd.bdate_range("2020-01-01", "2021-12-31")


def _spy(conn, days=DAYS) -> pd.Series:
    """A trending SPY: rises through 2020, falls through 2021 (in reais)."""
    px = pd.Series(range(len(days)), index=days, dtype=float)
    px = 100 + px.where(px < 260, 520 - px)
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        dict(market="US", symbol=s, date=d.date(), open=p / 5, high=p / 5, low=p / 5, close=p / 5,
             volume=1e7, adj_close=p / 5, split_adj_close=p / 5, close_brl=p,
             adj_close_brl=p, fx_rate=5.0, source="t")
        for s in ("SPY", "AAPL") for d, p in px.items()]))
    return px


@pytest.fixture
def plane(tmp_path, monkeypatch) -> str:
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    _spy(conn)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)
    return db


def _store_spx(db: str, level: float = 7.0) -> None:
    conn = sqlite3.connect(db)
    for d in DAYS:
        conn.execute("INSERT INTO external_prices (ticker, isin_code, date, open, high, low,"
                     " close, volume, source) VALUES ('$SPX', 'UNKNOWN', ?, NULL, NULL, NULL,"
                     " ?, 0, 'ALPHAVANTAGE_SPY_BRL')", (d.date().isoformat(), level))
    conn.commit()
    conn.close()


def test_an_empty_spx_falls_back_to_spy_off_the_us_plane(plane):
    px = download_benchmark("$SPX", "2020-01-01", "2021-12-31", market="US")
    assert len(px) == len(DAYS)
    assert px.iloc[0] == 100.0                       # adj_close_brl, not the USD close
    assert px.attrs["proxy_for"] == "$SPX"


def test_a_stored_spx_is_still_the_benchmark(plane):
    _store_spx(plane)
    px = download_benchmark("$SPX", "2020-01-01", "2021-12-31", market="US")
    assert (px == 7.0).all() and "proxy_for" not in px.attrs


def test_only_the_markets_own_benchmark_falls_back(plane):
    """Another `$` series that is empty stays empty: the proxy is for `$SPX`."""
    assert download_benchmark("$CDI", "2020-01-01", "2021-12-31", market="US").empty
    assert download_benchmark("$IBOV", "2020-01-01", "2021-12-31", market="B3").empty


def test_the_us_market_names_its_proxy():
    assert markets.MARKETS["US"].benchmark_proxy == "SPY"
    assert markets.MARKETS["B3"].benchmark_proxy is None
    assert markets.MARKETS["CRYPTO"].benchmark_proxy is None


def test_trend_flags_on_us_are_read_from_the_proxy(plane, monkeypatch):
    """The bug: with no `$SPX`, bench_above was empty, so it never read True."""
    import backtests.core.shared_data as sd
    monkeypatch.setattr(sd, "download_cdi_daily", lambda s, e: pd.Series(0.0, index=DAYS))
    shared = build_shared_data(plane, "2020-01-01", "2021-12-31", "ME", market="US")
    above = shared["bench_above"].dropna()
    assert above.any() and not above.all()            # up in 2020, down by late 2021
    assert {"code": "benchmark_proxy_from_plane",
            "detail": {"market": "US", "ticker": "$SPX", "proxy": "SPY"}} in shared["data_warnings"]
    assert shared["bench_drawdown"].max() > 0.3       # the same ruler, full history


def test_a_run_without_any_benchmark_says_so():
    spec = markets.MARKETS["B3"]
    assert _benchmark_notes(spec, pd.Series(dtype=float), "2020-01-01", "2021-12-31") == [
        {"code": "benchmark_missing", "detail": {"market": "B3", "ticker": "^BVSP"}}]
    early = pd.Series(1.0, index=pd.bdate_range("2010-01-01", periods=5))
    assert _benchmark_notes(spec, early, "2020-01-01", "2021-12-31")[0]["code"] == "benchmark_missing"


def test_a_normal_benchmark_adds_no_note():
    px = pd.Series(1.0, index=DAYS)
    assert _benchmark_notes(markets.MARKETS["B3"], px, "2020-01-01", "2021-12-31") == []
