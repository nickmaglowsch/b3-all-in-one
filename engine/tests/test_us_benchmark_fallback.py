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


def _store_spx(db: str, level: float = 7.0, days=DAYS) -> None:
    conn = sqlite3.connect(db)
    for d in days:
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


# ── a stale `$SPX`: it stops, SPY goes on ─────────────────────────────────────

SPX_STOPS = pd.Timestamp("2020-12-31")


def test_a_stale_spx_is_carried_to_the_end_by_spys_returns(plane):
    """The job failing *after* a while left `$SPX` ending early. Not empty, so
    the old fallback never fired, and every flag read False over the tail."""
    _store_spx(plane, days=DAYS[DAYS <= SPX_STOPS])
    px = download_benchmark("$SPX", "2020-01-01", "2021-12-31", market="US")
    spy = download_benchmark("SPY", "2020-01-01", "2021-12-31", market="US")
    assert px.index[-1] == DAYS[-1]                          # reaches the end
    assert (px.loc[:SPX_STOPS] == 7.0).all()                 # its own prints kept
    tail = px.loc[px.index > SPX_STOPS]
    # Rescaled to meet $SPX where it stops, moving with SPY from there.
    assert tail.iloc[0] == pytest.approx(7.0 * spy[tail.index[0]] / spy[SPX_STOPS])
    assert tail.pct_change().dropna().values == pytest.approx(
        spy.loc[tail.index].pct_change().dropna().values)
    assert px.attrs == {"proxy_for": "$SPX", "proxy_from": tail.index[0].date().isoformat()}


def test_a_spx_a_few_days_short_is_left_alone(plane):
    """A holiday or a late job is not staleness: nothing is spliced."""
    _store_spx(plane, days=DAYS[:-3])
    px = download_benchmark("$SPX", "2020-01-01", "2021-12-31", market="US")
    assert (px == 7.0).all() and "proxy_for" not in px.attrs


def test_a_run_on_a_stale_spx_says_where_the_proxy_took_over(plane, monkeypatch):
    import backtests.core.shared_data as sd
    monkeypatch.setattr(sd, "download_cdi_daily", lambda s, e: pd.Series(0.0, index=DAYS))
    _store_spx(plane, days=DAYS[DAYS <= SPX_STOPS])
    shared = build_shared_data(plane, "2020-01-01", "2021-12-31", "ME", market="US")
    assert shared["bench_px"].index[-1] == DAYS[-1]
    assert shared["bench_above"].loc["2021-02-01":].notna().all()
    [note] = [n for n in shared["data_warnings"] if n["code"].startswith("benchmark")]
    assert note == {"code": "benchmark_proxy_from_plane",
                    "detail": {"market": "US", "ticker": "$SPX", "proxy": "SPY",
                               "from": "2021-01-01"}}


def test_a_stale_benchmark_with_no_proxy_is_said():
    """B3 has no proxy: a `$IBOV` that stops early cannot be completed, so the
    run says the flags read nothing past its last print."""
    spec = markets.MARKETS["B3"]
    px = pd.Series(1.0, index=DAYS[DAYS <= SPX_STOPS])
    assert _benchmark_notes(spec, px, "2020-01-01", "2021-12-31", DAYS[-1]) == [
        {"code": "benchmark_stale",
         "detail": {"market": "B3", "ticker": "^BVSP", "last": "2020-12-31"}}]
    # Within a week of the plane's last session it is just a late print.
    assert _benchmark_notes(spec, pd.Series(1.0, index=DAYS[:-3]), "2020-01-01",
                            "2021-12-31", DAYS[-1]) == []


def test_the_bench_chart_says_its_sp500_line_is_spliced_from_spy(plane):
    """/benchmarks draws sp500 off the same reader: a line partly made of SPY
    says so, with the date it took over."""
    from scripts import bench_series
    _store_spx(plane, days=DAYS[DAYS <= SPX_STOPS])
    out = bench_series.series("2020-01-01", "2021-12-31", ["sp500", "cdi"])
    assert out["proxied"] == {"sp500": {"proxy": "SPY", "from": "2021-01-01"}}
    assert max(out["sp500"]) == DAYS[-1].strftime("%Y-%m-%d")


def test_a_bench_chart_on_a_whole_spx_names_no_proxy(plane):
    from scripts import bench_series
    _store_spx(plane)
    assert "proxied" not in bench_series.series("2020-01-01", "2021-12-31", ["sp500"])
