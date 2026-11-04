"""The accumulation tilt on any asset, measured against its own all-time high.

`bench_drawdown` + rule `exposure` (test_cheapness_regime.py) gate the book on
the *market's benchmark* — BTC on crypto, the Ibovespa on B3, the S&P on US — so
an ETH or PETR4 book was being sized by somebody else's drawdown. This is the
per-name half:

  * ``ath_drawdown`` — each name's ``1 - close / running max``, the running max
    over that name's whole history on the plane (not the backtest's load window);
  * ``exposure_expr`` — an expression evaluated like ``weight_expr``; each held
    name keeps ``clip(value, 0, 1)`` of its weight and the rest sits in CDI. With
    ``"ath_drawdown"`` that is the indicator's own mapping: deploy fraction =
    how far below the high.
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
from backtests.core import warmup_size
from backtests.core.config_strategy import RankAndHold
from backtests.core.shared_data import build_shared_data
from scripts.validate_spec import validate

ROOT = Path(__file__).resolve().parents[1]


def _own(**extra) -> dict:
    spec = {"name": "OwnTilt", "kind": "rank_and_hold", "market": "CRYPTO",
            "rebalance": "ME",
            "universe": {"min_adtv": 0, "min_price": 0, "tickers": ["ETHUSDT"]},
            "signal": {"factor": "adtv", "apply_glitch": False},
            "selection": {"top_n": 1, "min_names": 1},
            "exposure_expr": "ath_drawdown"}
    spec.update(extra)
    return spec


def _rows(symbol: str, prices: pd.Series) -> list[dict]:
    return [{"market": "CRYPTO", "symbol": symbol, "date": d.date(), "open": p, "high": p,
             "low": p, "close": p, "volume": 1e6, "adj_close": p, "split_adj_close": p,
             "close_brl": p * 5.0, "adj_close_brl": p * 5.0, "fx_rate": 5.0, "source": "t"}
            for d, p in prices.items()]


# ── ath_drawdown ─────────────────────────────────────────────────────────────

@pytest.fixture
def two_coin_plane(tmp_path, monkeypatch) -> str:
    """ETH peaks at 400 in Nov 2021 and sits at 100 through 2022; BTC never
    falls. A run loaded from 2022 must still see ETH 75% below its high."""
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    days = pd.date_range("2021-01-01", "2022-03-31", freq="D")
    eth = pd.Series(200.0, index=days)
    eth[(days >= "2021-11-01") & (days < "2021-11-10")] = 400.0   # a mid-month peak
    eth[days >= "2022-01-01"] = 100.0
    market_storage.upsert_market_prices(conn, pd.DataFrame(
        _rows("ETHUSDT", eth) + _rows("BTCUSDT", pd.Series(np.linspace(100, 200, len(days)),
                                                            index=days))))
    conn.close()

    import backtests.core.shared_data as sd
    win = pd.date_range("2022-01-01", "2022-03-31", freq="D")
    monkeypatch.setattr(sd, "download_benchmark",
                        lambda t, s, e, *, market="B3": pd.Series(1.0, index=win))
    monkeypatch.setattr(sd, "download_cdi_daily", lambda s, e: pd.Series(0.0, index=win))
    return db


def test_each_name_is_measured_from_its_own_high_before_the_window(two_coin_plane):
    shared = build_shared_data(two_coin_plane, "2022-01-01", "2022-03-31", "ME",
                               market="CRYPTO")
    assert "ath_drawdown" not in dict.keys(shared)        # deferred: built on first read
    dd = shared["ath_drawdown"]
    # The peak is a daily close in the middle of November, not a month-end one.
    assert dd.loc["2022-02-28", "ETHUSDT"] == pytest.approx(0.75)
    assert dd.loc["2022-02-28", "BTCUSDT"] == pytest.approx(0.0)
    assert list(dd.columns) == list(shared["ret"].columns)
    assert dd.index.equals(shared["ret"].index)


# ── exposure_expr ────────────────────────────────────────────────────────────

def _book(dd: pd.DataFrame) -> dict:
    idx, cols = dd.index, dd.columns
    return {"ret": pd.DataFrame(0.01, index=idx, columns=cols),
            "adtv": pd.DataFrame(1e9, index=idx, columns=cols),
            "raw_close": pd.DataFrame(1.0, index=idx, columns=cols),
            "has_glitch": pd.DataFrame(0, index=idx, columns=cols),
            "cdi_monthly": pd.Series(0.01, index=idx),
            "ath_drawdown": dd}


def test_each_held_name_keeps_its_own_fraction_and_cdi_takes_the_rest():
    idx = pd.date_range("2022-01-31", periods=4, freq="ME")
    dd = pd.DataFrame({"ETHUSDT": [0.0, 0.8, 0.3, np.nan],
                       "SOLUSDT": [0.5, 0.2, 1.4, 0.6]}, index=idx)
    spec = _own(universe={"min_adtv": 0, "min_price": 0, "tickers": ["ETHUSDT", "SOLUSDT"]},
                selection={"top_n": 2, "min_names": 1})
    _, tw = RankAndHold(spec).generate_signals(_book(dd), {})
    # Row i reads row i-1; each name has half the book before its own scale.
    assert tw.iloc[2]["ETHUSDT"] == pytest.approx(0.4)          # 0.5 x 0.8
    assert tw.iloc[2]["SOLUSDT"] == pytest.approx(0.1)          # 0.5 x 0.2
    assert tw.iloc[2]["CDI_ASSET"] == pytest.approx(0.5)
    assert tw.iloc[3]["SOLUSDT"] == pytest.approx(0.5)          # 1.4 clipped to 1
    assert tw.iloc[3]["ETHUSDT"] == pytest.approx(0.15)
    for i in (2, 3):
        assert tw.iloc[i].sum() == pytest.approx(1.0)


def test_a_name_the_expression_cannot_score_holds_nothing():
    idx = pd.date_range("2022-01-31", periods=3, freq="ME")
    dd = pd.DataFrame({"ETHUSDT": [np.nan, np.inf, 0.5]}, index=idx)
    _, tw = RankAndHold(_own()).generate_signals(_book(dd), {})
    assert tw.iloc[1]["ETHUSDT"] == 0.0 and tw.iloc[1]["CDI_ASSET"] == 1.0
    assert tw.iloc[2]["ETHUSDT"] == 0.0 and tw.iloc[2]["CDI_ASSET"] == 1.0


def test_it_stacks_with_a_rules_exposure():
    idx = pd.date_range("2022-01-31", periods=3, freq="ME")
    dd = pd.DataFrame({"ETHUSDT": [0.5, 0.5, 0.5]}, index=idx)
    spec = _own(regime={"add_ibov": False, "inputs": {},
                        "rules": [{"default": True, "exposure": 0.5}]})
    _, tw = RankAndHold(spec).generate_signals(_book(dd), {})
    assert tw.iloc[2]["ETHUSDT"] == pytest.approx(0.25)
    assert tw.iloc[2]["CDI_ASSET"] == pytest.approx(0.75)


def test_an_expression_with_a_floor_reads_like_the_indicators_zones():
    """`clip(0.2 + ath_drawdown, 0, 1)`: 20% at the high, all in 80% below it."""
    idx = pd.date_range("2022-01-31", periods=3, freq="ME")
    dd = pd.DataFrame({"ETHUSDT": [0.0, 0.9, 0.0]}, index=idx)
    _, tw = RankAndHold(_own(exposure_expr="clip(0.2 + ath_drawdown, 0, 1)")
                        ).generate_signals(_book(dd), {})
    assert tw.iloc[1]["ETHUSDT"] == pytest.approx(0.2)
    assert tw.iloc[2]["ETHUSDT"] == pytest.approx(1.0)


def test_a_spec_without_it_has_no_cdi_column_as_before():
    idx = pd.date_range("2022-01-31", periods=3, freq="ME")
    spec = _own()
    del spec["exposure_expr"]
    r, tw = RankAndHold(spec).generate_signals(_book(pd.DataFrame({"ETHUSDT": 0.5}, index=idx)), {})
    assert "CDI_ASSET" not in tw.columns and "CDI_ASSET" not in r.columns
    assert tw.iloc[2]["ETHUSDT"] == 1.0


# ── validation and warm-up ────────────────────────────────────────────────────

def test_the_own_drawdown_tilt_validates():
    r = validate(_own())
    assert r["ok"], r["errors"]


@pytest.mark.parametrize("bad,msg", [
    (0.5, "exposure_expr must be an expression string"),
    ("", "exposure_expr must be an expression string"),
    ("0.5", "reads no market data"),
    ("nope(ath_drawdown)", "unknown function"),
])
def test_a_bad_exposure_expr_is_refused(bad, msg):
    r = validate(_own(exposure_expr=bad))
    assert not r["ok"] and any(msg in e for e in r["errors"]), r["errors"]


def test_a_var_may_not_take_the_frames_name():
    spec = _own(signal={"factor": "adtv", "apply_glitch": False,
                        "vars": {"ath_drawdown": "ret"}})
    assert not validate(spec)["ok"]


def test_the_warm_up_counts_the_exposure_expressions_window():
    base = RankAndHold(_own())
    longer = RankAndHold(_own(exposure_expr="roll_mean(ath_drawdown, 24)"))
    assert (warmup_size.strategy_months(longer, {}, "ME")
            > warmup_size.strategy_months(base, {}, "ME") + 20)


# ── end to end ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def eth_plane(tmp_path_factory) -> str:
    db = str(tmp_path_factory.mktemp("plane") / "crypto.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    rng = np.random.default_rng(11)
    days = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    rows = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        rows += _rows(sym, pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.04, len(days)))),
                                     index=days))
    market_storage.upsert_market_prices(conn, pd.DataFrame(rows))
    conn.close()
    return db


def _run(plane: str, tmp_path, spec: dict) -> pd.DataFrame:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    (specs / "s.yaml").write_text(json.dumps(spec))
    cfg = {"start": "2022-01-01", "end": "2023-12-31", "freq": "ME", "market": "CRYPTO"}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
         spec["name"], str(tmp_path / "out"), json.dumps(cfg)],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
        env={**os.environ, "ENGINE_DATABASE_URL": plane, "ENGINE_BENCH_OFFLINE": "1",
             "ENGINE_USER_SPECS_DIR": str(specs)})
    assert proc.returncode == 0, proc.stderr[-2000:]
    return pd.read_csv(tmp_path / "out" / "curves.csv")


def test_an_eth_book_sized_by_its_own_drawdown_runs_end_to_end(eth_plane, tmp_path):
    """The benchmark is stubbed flat offline, so a bench_drawdown book could not
    move here; ath_drawdown reads the plane itself. Held partly in CDI most of
    the time, the tilted book is calmer than the all-in one — a dropped
    exposure_expr would make the two the same run."""
    tilt = _run(eth_plane, tmp_path / "a", _own(name="OwnTilt"))
    full = _own(name="OwnFull")
    del full["exposure_expr"]
    full_curve = _run(eth_plane, tmp_path / "b", full)
    vol = lambda c: c["pretax"].pct_change().std()
    assert (tilt["pretax"] > 0).all()
    assert vol(tilt) < vol(full_curve)
