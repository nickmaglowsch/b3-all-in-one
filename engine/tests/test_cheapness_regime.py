"""Partial exposure, drawdown-from-peak and a hand-picked universe.

Three small pieces that together let a spec say "hold BTC, and hold more of it
the further it sits below its all-time high" — the accumulation tilt of a
drawdown/MVRV indicator, minus the MVRV the plane does not carry:

  * ``bench_drawdown`` — 1 - benchmark / its running max, read off the
    benchmark's *full* history rather than the backtest's load window;
  * a regime rule's ``exposure`` — invest that fraction of the book, the rest in
    CDI, where before a rule could only invest all of it or park all of it;
  * ``universe.tickers`` — the universe is these names only. Without it the
    nearest thing to "just BTC" on the crypto plane was the top pair by
    turnover, which is a stablecoin pair (USDCUSDT, FDUSDUSDT) as often as not.
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
from backtests.core import regimes, sleeve_tickers
from backtests.core.config_strategy import RankAndHold, _eval_regime
from backtests.core.shared_data import build_shared_data
from scripts.validate_spec import report, validate

ROOT = Path(__file__).resolve().parents[1]

_ZONES = {
    "inputs": {"deep": {"flag": "bench_drawdown", "offset": 1, "gt": 0.50},
               "cheap": {"flag": "bench_drawdown", "offset": 1, "gt": 0.25}},
    "rules": [{"deep": True, "exposure": 1.0},
              {"cheap": True, "exposure": 0.6},
              {"default": True, "exposure": 0.2}],
}


def _tilt(**extra) -> dict:
    spec = {"name": "Tilt", "kind": "rank_and_hold", "market": "CRYPTO",
            "rebalance": "ME", "universe": {"min_adtv": 0, "min_price": 0,
                                            "tickers": ["BTCUSDT"]},
            "signal": {"factor": "adtv", "apply_glitch": False},
            "selection": {"top_n": 1, "min_names": 1},
            "regime": {"add_ibov": False, **_ZONES}}
    spec.update(extra)
    return spec


# ── drawdown from the peak ────────────────────────────────────────────────────

def test_drawdown_is_measured_from_the_running_high_at_each_period_end():
    days = pd.bdate_range("2020-01-01", "2020-04-30")
    px = pd.Series(100.0, index=days)
    px[days >= "2020-02-01"] = 200.0          # a new high in February
    px[days >= "2020-03-01"] = 50.0           # -75% from it in March
    px[days >= "2020-04-01"] = 150.0          # recovering, still below
    dd = regimes.drawdown_from_peak(px, "ME")
    assert dd.loc["2020-01-31"] == 0.0
    assert dd.loc["2020-02-29"] == 0.0
    assert dd.loc["2020-03-31"] == pytest.approx(0.75)
    assert dd.loc["2020-04-30"] == pytest.approx(0.25)


def test_drawdown_is_causal_a_later_high_does_not_reach_back():
    days = pd.bdate_range("2020-01-01", "2020-03-31")
    px = pd.Series(np.linspace(100, 50, len(days)), index=days)
    later = pd.concat([px, pd.Series(1000.0, index=pd.bdate_range("2020-04-01", "2020-04-30"))])
    assert regimes.drawdown_from_peak(px, "ME").equals(
        regimes.drawdown_from_peak(later, "ME").loc[:"2020-03-31"])


def test_drawdown_of_an_empty_or_unpriced_benchmark_is_empty_not_an_error():
    assert regimes.drawdown_from_peak(pd.Series(dtype=float), "ME").empty
    days = pd.bdate_range("2020-01-01", periods=5)
    assert regimes.drawdown_from_peak(pd.Series(np.nan, index=days), "ME").empty


def test_bench_drawdown_reads_the_peak_from_before_the_load_window(tmp_path, monkeypatch):
    """The whole point of its own fetch: a run loaded from 2022 must still know
    the 2021 high, or the bottom of a crash reads as a fresh peak (drawdown 0)."""
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    dates = pd.date_range("2022-01-01", "2022-03-31", freq="D")
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        dict(market="CRYPTO", symbol="BTCUSDT", date=d.date(), open=1.0, high=1.0,
             low=1.0, close=1.0, volume=1e6, adj_close=1.0, split_adj_close=1.0,
             close_brl=5.0, adj_close_brl=5.0, fx_rate=5.0, source="t")
        for d in dates]))
    conn.close()

    import backtests.core.shared_data as sd
    calls = []

    def bench(ticker, start, end, *, market="B3"):
        calls.append(start)
        days = pd.bdate_range(max("2021-01-01", start), "2022-03-31")
        px = pd.Series(100.0, index=days)
        px[days >= "2021-11-01"] = 200.0       # the all-time high, before the window
        px[days >= "2022-01-01"] = 80.0        # -60% inside it
        return px[px.index >= start]

    monkeypatch.setattr(sd, "download_benchmark", bench)
    monkeypatch.setattr(sd, "download_cdi_daily", lambda s, e: pd.Series(0.0, index=dates))
    shared = build_shared_data(db, "2022-01-01", "2022-03-31", "ME", market="CRYPTO")
    assert calls == ["2022-01-01"]                      # deferred: not fetched yet
    assert shared["bench_drawdown"].loc["2022-02-28"] == pytest.approx(0.6)
    assert calls[-1] == sd._PEAK_HISTORY_FROM


# ── exposure ──────────────────────────────────────────────────────────────────

def _one_coin_shared(dd: pd.Series) -> dict:
    idx = dd.index
    cols = ["BTCUSDT", "USDCUSDT"]
    return {"ret": pd.DataFrame(0.01, index=idx, columns=cols),
            "log_ret": pd.DataFrame(np.log1p(0.01), index=idx, columns=cols),
            # The stablecoin is the deeper book — what an adtv top-1 would pick.
            "adtv": pd.DataFrame({"BTCUSDT": 1e9, "USDCUSDT": 5e9}, index=idx),
            "raw_close": pd.DataFrame(1.0, index=idx, columns=cols),
            "has_glitch": pd.DataFrame(0, index=idx, columns=cols),
            "cdi_monthly": pd.Series(0.01, index=idx),
            "bench_drawdown": dd}


def test_exposure_splits_the_book_between_the_holding_and_cdi():
    idx = pd.date_range("2021-01-31", periods=8, freq="ME")
    dd = pd.Series([0.0, 0.1, 0.3, 0.6, 0.7, 0.4, 0.05, 0.0], index=idx)
    _, tw = RankAndHold(_tilt()).generate_signals(_one_coin_shared(dd), {})
    # offset 1: row i reads the drawdown at row i-1.
    want = {2: 0.2, 3: 0.6, 4: 1.0, 5: 1.0, 6: 0.6, 7: 0.2}
    for i, btc in want.items():
        assert tw["BTCUSDT"].iloc[i] == pytest.approx(btc), i
        assert tw["CDI_ASSET"].iloc[i] == pytest.approx(1.0 - btc), i
        assert tw.iloc[i].sum() == pytest.approx(1.0)
    # The allow-list held: the stablecoin, the deeper book, is never bought.
    assert (tw["USDCUSDT"] == 0.0).all()


def test_exposure_zero_is_the_same_as_park():
    idx = pd.date_range("2021-01-31", periods=5, freq="ME")
    spec = _tilt(regime={"add_ibov": False, "inputs": {},
                         "rules": [{"default": True, "exposure": 0.0}]})
    _, tw = RankAndHold(spec).generate_signals(_one_coin_shared(pd.Series(0.0, index=idx)), {})
    assert (tw["CDI_ASSET"].iloc[2:] == 1.0).all()
    assert (tw["BTCUSDT"] == 0.0).all()


def test_a_rule_without_exposure_still_invests_everything():
    idx = pd.date_range("2021-01-31", periods=5, freq="ME")
    spec = _tilt(regime={"add_ibov": False, "inputs": {}, "rules": [{"default": True}]})
    _, tw = RankAndHold(spec).generate_signals(_one_coin_shared(pd.Series(0.0, index=idx)), {})
    assert (tw["BTCUSDT"].iloc[2:] == 1.0).all()
    assert (tw["CDI_ASSET"] == 0.0).all()


def test_exposure_is_an_action_not_a_condition():
    """A rule's `exposure` key must not be read as an input to match on — a rule
    {"cheap": true, "exposure": 0.6} would otherwise never match anything."""
    idx = pd.date_range("2021-01-31", periods=3, freq="ME")
    shared = {"bench_drawdown": pd.Series(0.3, index=idx), "ret": pd.DataFrame(index=idx)}
    assert _eval_regime(_ZONES, shared, 2) == {"cheap": True, "exposure": 0.6}


def test_exposure_scales_a_many_name_book_too():
    idx = pd.date_range("2021-01-31", periods=4, freq="ME")
    cols = ["A", "B", "C", "D"]
    shared = {"ret": pd.DataFrame(0.01, index=idx, columns=cols),
              "adtv": pd.DataFrame([[4e6, 3e6, 2e6, 1e6]] * 4, index=idx, columns=cols),
              "raw_close": pd.DataFrame(10.0, index=idx, columns=cols),
              "has_glitch": pd.DataFrame(0, index=idx, columns=cols),
              "cdi_monthly": pd.Series(0.01, index=idx)}
    spec = {"name": "X", "kind": "rank_and_hold", "rebalance": "ME",
            "universe": {"min_adtv": 0, "min_price": 0},
            "signal": {"factor": "adtv", "apply_glitch": False},
            "selection": {"top_n": 2, "min_names": 1},
            "regime": {"add_ibov": False, "inputs": {},
                       "rules": [{"default": True, "exposure": 0.5}]}}
    _, tw = RankAndHold(spec).generate_signals(shared, {})
    row = tw.iloc[2]
    assert row["A"] == pytest.approx(0.25) and row["B"] == pytest.approx(0.25)
    assert row["CDI_ASSET"] == pytest.approx(0.5)


def test_the_allow_list_takes_the_callers_spelling():
    """`BTC` is the crypto market's alias for BTCUSDT, as it is for a benchmark."""
    idx = pd.date_range("2021-01-31", periods=4, freq="ME")
    spec = _tilt(universe={"min_adtv": 0, "min_price": 0, "tickers": ["btc"]})
    _, tw = RankAndHold(spec).generate_signals(_one_coin_shared(pd.Series(0.0, index=idx)), {})
    assert tw["BTCUSDT"].iloc[2] == pytest.approx(0.2)


# ── validation ────────────────────────────────────────────────────────────────

def test_a_zoned_exposure_regime_is_valid():
    r = validate(_tilt())
    assert r["ok"], r["errors"]


@pytest.mark.parametrize("bad", [1.5, -0.1, "half", True, None])
def test_an_exposure_outside_zero_one_is_rejected(bad):
    spec = _tilt(regime={"inputs": {}, "rules": [{"default": True, "exposure": bad}]})
    r = validate(spec)
    assert not r["ok"] and any("exposure must be a number in [0, 1]" in e for e in r["errors"])


def test_exposure_beside_park_is_rejected():
    spec = _tilt(regime={"inputs": {}, "rules": [{"default": True, "park": True,
                                                  "exposure": 0.5}]})
    r = validate(spec)
    assert not r["ok"] and any("contradict" in e for e in r["errors"])


@pytest.mark.parametrize("bad", [[], "BTCUSDT", [""], [1], ["X"] * 51])
def test_a_malformed_ticker_allow_list_is_rejected(bad):
    spec = _tilt(universe={"min_adtv": 0, "min_price": 0, "tickers": bad})
    r = validate(spec)
    assert not r["ok"] and any("universe.tickers" in e for e in r["errors"])


@pytest.fixture
def crypto_plane(tmp_path, monkeypatch) -> str:
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    rows = [dict(market="CRYPTO", symbol=s, date=d.date(), open=1.0, high=1.0, low=1.0,
                 close=1.0, volume=1e6, adj_close=1.0, split_adj_close=1.0,
                 close_brl=5.0, adj_close_brl=5.0, fx_rate=5.0, source="t")
            for s in ("BTCUSDT", "BTCFDUSD")
            for d in pd.date_range("2021-01-01", periods=3, freq="D")]
    market_storage.upsert_market_prices(conn, pd.DataFrame(rows))
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)
    return db


def test_a_ticker_on_the_plane_passes_the_save_check(crypto_plane):
    assert sleeve_tickers.check_universe(_tilt()) == []
    r = report(_tilt())
    assert r["ok"], r["errors"]


def test_an_unknown_ticker_is_refused_by_name(crypto_plane):
    spec = _tilt(universe={"min_adtv": 0, "min_price": 0, "tickers": ["BTCUSDT", "BTCUSD"]})
    r = report(spec)
    assert not r["ok"]
    assert r["errors"] == [(
        "universe.tickers: unknown ticker 'BTCUSD' — the CRYPTO price plane has no "
        "prices for it (crypto pairs are quoted in USDT, e.g. BTCUSDT)")]


def test_a_pair_outside_the_markets_universe_shape_is_refused(crypto_plane):
    """BTCFDUSD is priced on the plane but the crypto universe is the USDT pairs,
    so the backtest would never see it."""
    spec = _tilt(universe={"min_adtv": 0, "min_price": 0, "tickers": ["BTCFDUSD"]})
    assert not report(spec)["ok"]


def test_an_unreadable_plane_is_an_error_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DATABASE_URL", str(tmp_path / "missing.sqlite"))
    assert sleeve_tickers.check_universe(_tilt()) == [
        "could not check universe.tickers: the CRYPTO price plane could not be read"]


# ── end to end, through the entry point the platform shells ──────────────────

@pytest.fixture(scope="module")
def btc_plane(tmp_path_factory) -> str:
    db = str(tmp_path_factory.mktemp("plane") / "crypto.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    rng = np.random.default_rng(3)
    dates = pd.date_range("2021-01-01", "2023-12-31", freq="D")
    rows = []
    for sym, vol in (("BTCUSDT", 0.04), ("USDCUSDT", 0.0)):
        px = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, vol, len(dates)))) if vol else \
            np.full(len(dates), 1.0)
        rows.extend(dict(market="CRYPTO", symbol=sym, date=d.date(), open=p, high=p,
                         low=p, close=p, volume=5e6 if vol else 5e7, adj_close=p,
                         split_adj_close=p, close_brl=p * 5.0, adj_close_brl=p * 5.0,
                         fx_rate=5.0, source="synthetic")
                    for d, p in zip(dates, px))
    market_storage.upsert_market_prices(conn, pd.DataFrame(rows))
    conn.close()
    return db


def _run(plane: str, tmp_path, spec: dict) -> pd.DataFrame:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    (specs / "tilt.yaml").write_text(json.dumps(spec))
    out = tmp_path / "out"
    cfg = {"start": "2021-06-01", "end": "2023-12-31", "freq": "ME", "market": "CRYPTO"}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         spec["name"], str(out), json.dumps(cfg)],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
        env={**os.environ, "ENGINE_DATABASE_URL": plane, "ENGINE_BENCH_OFFLINE": "1",
             "ENGINE_USER_SPECS_DIR": str(specs)})
    assert proc.returncode == 0, proc.stderr[-2000:]
    return pd.read_csv(out / "curves.csv")


def test_a_partly_invested_btc_book_runs_end_to_end(btc_plane, tmp_path):
    """The offline benchmark is flat (drawdown 0), so every row takes the default
    rule: 20% BTC. That book must be visibly calmer than the all-in one — a
    dropped `exposure` would make the two runs identical."""
    part = _run(btc_plane, tmp_path / "a", _tilt(name="TiltPart"))
    full = _run(btc_plane, tmp_path / "b", _tilt(
        name="TiltFull", regime={"add_ibov": False, "inputs": {},
                                 "rules": [{"default": True, "exposure": 1.0}]}))
    vol = lambda c: c["pretax"].pct_change().std()
    assert (part["pretax"] > 0).all()
    assert vol(part) < 0.5 * vol(full)
