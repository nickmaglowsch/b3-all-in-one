"""Signal warm-up comes from before `start`, not from inside the window.

A momentum book asked for 2019 used to spend 2019 and early 2020 building its
lookback, in cash, and so never saw the March 2020 crash. These run the real
entry point the platform shells over a synthetic plane whose history opens four
years before the window and that crashes inside it, and check:

  (a) a rank_and_hold book is invested from the window's first rebalance, and
      every pre-tax period return from there on equals a longer run's;
  (b) the same for a fixed_weight blend with a nested strategy sleeve;
  (c) nothing — NAV mark, trade, tax — lands before `start`;
  (d) a window opening at the first bar is the old run, plus the warning;
  (e) the pre-roll reads no future: weights at t ignore every bar after t.

Why only *pre-tax* returns are compared in (a)/(b): with slippage at zero, the
same weights over the same returns give the same pre-tax path, so they must
match to float precision. The after-tax path is not supposed to: the longer run
carries lots and a loss carryforward into the window that the shorter run never
had.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from b3_pipeline import market_storage, storage
from backtests.core import shared_data as sd
from backtests.core import universe, warmup
from backtests.core.config_strategy import FixedWeight, RankAndHold
from backtests.core.strategy_registry import get_registry

ROOT = Path(__file__).resolve().parents[1]
FIRST_BAR, LAST_BAR = "2015-01-01", "2022-12-31"
SYMBOLS = tuple(f"S{i:02d}USDT" for i in range(10))
CRASH = ("2020-02-24", "2020-03-20")

MOM = {
    "name": "WarmMom", "kind": "rank_and_hold", "market": "CRYPTO",
    "rebalance": "ME", "warmup_pad": 2,
    "universe": {"min_adtv": 0, "min_price": 0},
    "signal": {"factor": "sharpe_mom", "lookback": 12},
    "selection": {"top_n": 3, "min_names": 3}, "weighting": "equal",
}
# Quarterly, like the real 40/40/20 blend — so the nested momentum sleeve's
# twelve-period lookback is twelve *quarters*, the long warm-up this is about.
BLEND = {
    "name": "WarmBlend", "kind": "fixed_weight", "market": "CRYPTO",
    "rebalance": "QE", "park_in_cdi_until_live": True,
    "sleeves": [{"strategy": "WarmMom", "weight": 0.4},
                {"ticker": "IVVB11.SA", "weight": 0.4},
                {"asset": "CDI", "weight": 0.2}],
}
# Momentum that drops a name after a month worse than -20%. The crash empties
# the universe, so the rows after it are "hold" rows (fewer than min_names).
THIN = {**MOM, "name": "ThinMom",
        "signal": {"expr": "where(roll_sum(shift(log_ret, 1), lookback), "
                           "shift(ret, 1) > -0.2)", "lookback": 12}}
# A bundle key inside a window: mf_composite is 13 rows of warm-up of its own
# before the 12-row mean even starts.
SMOOTH = {**MOM, "name": "SmoothMF", "warmup_pad": 1,
          "signal": {"expr": "roll_mean(mf_composite, 12)", "apply_glitch": False}}


def _prices(seed: int, perturb_after: str | None = None) -> pd.DataFrame:
    """Calm random walks with a persistent drift per name, and a market-wide
    crash inside the window. `perturb_after` scrambles every bar after it."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(FIRST_BAR, LAST_BAR, freq="D")
    crash = (dates >= CRASH[0]) & (dates <= CRASH[1])
    late = dates > pd.Timestamp(perturb_after) if perturb_after else None
    rows = []
    for k, sym in enumerate(SYMBOLS):
        steps = rng.normal(0.0002 * (k - 4), 0.01, len(dates))
        steps[crash] = -0.025
        if late is not None:
            steps[late] = np.random.default_rng(99 + k).normal(0.0, 0.03, late.sum())
        px = 100.0 * np.exp(np.cumsum(steps))
        rows.extend(
            dict(market="CRYPTO", symbol=sym, date=d.date(), open=p,
                 high=p * 1.01, low=p * 0.99, close=p, volume=5e6,
                 adj_close=p, split_adj_close=p, close_brl=p * 5.2,
                 adj_close_brl=p * 5.2, fx_rate=5.2, source="synthetic")
            for d, p in zip(dates, px))
    return pd.DataFrame(rows)


def _plane(path: Path, prices: pd.DataFrame) -> str:
    conn = sqlite3.connect(str(path))
    storage.init_db(conn)
    market_storage.upsert_market_prices(conn, prices)
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="CRYPTO", symbol=s, currency="USDT", listing_date=FIRST_BAR,
             delisting_date=None, is_worthless=0) for s in SYMBOLS]))
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("warmup")
    specs = root / "specs"
    specs.mkdir()
    (specs / "warm_mom.yaml").write_text(yaml.safe_dump(MOM))
    (specs / "warm_blend.yaml").write_text(yaml.safe_dump(BLEND))
    (specs / "thin_mom.yaml").write_text(yaml.safe_dump(THIN))
    (specs / "smooth_mf.yaml").write_text(yaml.safe_dump(SMOOTH))
    plane = _plane(root / "plane.sqlite", _prices(5))
    return {"root": root, "specs": str(specs), "plane": plane, "runs": {}}


def _run(world, strategy: str, start: str, months: str | None = None) -> tuple:
    """(payload, curves) of one run, memoised per module — each is a subprocess."""
    key = (strategy, start, months)
    if key not in world["runs"]:
        out = world["root"] / f"run{len(world['runs'])}"
        cfg = {"start": start, "end": LAST_BAR, "market": "CRYPTO",
               "params": {"slippage": 0.0}}
        env = {**os.environ, "ENGINE_DATABASE_URL": world["plane"],
               "ENGINE_BENCH_OFFLINE": "1", "ENGINE_USER_SPECS_DIR": world["specs"]}
        env.pop("ENGINE_WARMUP_MONTHS", None)
        if months is not None:
            env["ENGINE_WARMUP_MONTHS"] = months
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_backtest_json.py"),
             strategy, str(out), json.dumps(cfg)],
            capture_output=True, text=True, cwd=str(ROOT), env=env)
        assert proc.returncode == 0, proc.stderr[-2000:]
        payload = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        world["runs"][key] = (json.loads(payload[-1]),
                              pd.read_csv(out / "curves.csv", parse_dates=["date"]))
    return world["runs"][key]


def _period_returns(curves: pd.DataFrame, after: str) -> pd.Series:
    """Pre-tax period returns from `after` on (no contributions in these runs)."""
    s = curves.set_index("date")["pretax"]
    return s.pct_change().loc[s.index > pd.Timestamp(after)]


def _warnings(payload: dict) -> list:
    return [w for w in payload["data_warnings"] if w["code"] == warmup.WARNING]


# ── (a) a rank_and_hold book ──────────────────────────────────────────────────

def test_a_ranked_book_is_invested_from_the_first_rebalance(world):
    m, curves = _run(world, "WarmMom", "2019-01-01")
    assert m["invested_from"] == "2019-01-31"
    assert curves["date"].iloc[0] == pd.Timestamp("2019-01-31")
    assert not _warnings(m)
    # Sized from the spec: 12-period lookback + pad 2, monthly, plus the margin.
    assert m["warmup_months"] == 17


def test_it_earns_what_a_longer_run_earns_over_the_same_dates(world):
    _, s_curves = _run(world, "WarmMom", "2019-01-01")
    _, l_curves = _run(world, "WarmMom", "2016-06-01")
    a = _period_returns(s_curves, "2019-01-31")
    b = _period_returns(l_curves, "2019-01-31")
    assert len(a) == 47 and a.index.equals(b.index)
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=0, atol=1e-12)


def test_the_crash_inside_the_window_is_no_longer_sat_out(world):
    new, _ = _run(world, "WarmMom", "2019-01-01")
    old, _ = _run(world, "WarmMom", "2019-01-01", months="0")
    # The old run spent 14 months warming up, past the crash, and said nothing.
    assert old["invested_from"] == "2020-03-31"
    assert _warnings(old)[0]["detail"] == {
        "strategy": "WarmMom", "invested_from": "2020-03-31",
        "first_rebalance": "2019-01-31"}
    assert new["pretax"]["Max Drawdown (%)"] < old["pretax"]["Max Drawdown (%)"] - 20


# ── (b) a fixed_weight blend with a nested strategy sleeve ────────────────────

def test_a_blend_sleeve_warms_up_before_the_window(world):
    short, s_curves = _run(world, "WarmBlend", "2019-01-01")
    _, l_curves = _run(world, "WarmBlend", "2016-06-01")
    assert short["invested_from"] == "2019-03-31"
    assert not _warnings(short)
    # 14 quarters for the nested sleeve, plus the margin.
    assert short["warmup_months"] == 45
    a = _period_returns(s_curves, "2019-03-31")
    b = _period_returns(l_curves, "2019-03-31")
    assert len(a) == 15 and a.index.equals(b.index)
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=0, atol=1e-12)


def test_a_sleeve_warming_up_behind_cdi_is_still_reported(world):
    """The blend's own rows are never empty — the sleeve's share parks in CDI —
    so the sleeve's book is what says the blend was not yet invested."""
    old, curves = _run(world, "WarmBlend", "2019-01-01", months="0")
    assert curves["date"].iloc[0] == pd.Timestamp("2019-03-31")
    late = _warnings(old)
    assert [w["detail"]["strategy"] for w in late] == ["WarmMom"]
    assert old["invested_from"] == late[0]["detail"]["invested_from"] > "2019-03-31"
    new, _ = _run(world, "WarmBlend", "2019-01-01")
    assert new["pretax"]["Max Drawdown (%)"] < old["pretax"]["Max Drawdown (%)"]


# ── (c) nothing before start ──────────────────────────────────────────────────

@pytest.mark.parametrize("strategy", ["WarmMom", "WarmBlend"])
def test_no_mark_trade_or_tax_lands_before_start(world, strategy):
    m, curves = _run(world, strategy, "2019-01-01")
    assert (curves["date"] >= pd.Timestamp("2019-01-01")).all()
    assert m["start"] == "2019-01-01"
    assert m["periods"] == len(curves)
    # The inception mark is the capital, untouched by any pre-roll trade or tax.
    assert curves["pretax"].iloc[0] == pytest.approx(100_000.0)
    assert curves["tax_paid"].sum() == pytest.approx(m["total_tax_paid"])


# ── (d) a window that opens at the first bar ──────────────────────────────────

@pytest.mark.parametrize("strategy", ["WarmMom", "WarmBlend"])
def test_a_full_history_run_is_the_old_run_plus_the_warning(world, strategy):
    new, new_curves = _run(world, strategy, FIRST_BAR)
    old, old_curves = _run(world, strategy, FIRST_BAR, months="0")
    pd.testing.assert_frame_equal(new_curves, old_curves)
    volatile = {"peak_rss_mb", "warmup_months"}
    assert {k: v for k, v in new.items() if k not in volatile} == \
        {k: v for k, v in old.items() if k not in volatile}
    late = _warnings(new)
    assert late and late[0]["detail"]["strategy"] == "WarmMom"
    assert late[0]["detail"]["invested_from"] == new["invested_from"]


# ── (e) no look-ahead ─────────────────────────────────────────────────────────

def _book(monkeypatch, plane: str, spec=MOM, params=None,
          start="2019-01-01", loaded_from=None) -> pd.DataFrame:
    """A spec's in-window weights, in process, as the runner builds them —
    loaded from its own pre-roll, or from `loaded_from` when given."""
    def flat(s, e, *_a, **_k):
        return pd.Series(0.0, index=pd.bdate_range(s, e))
    monkeypatch.setattr(sd, "download_cdi_daily", flat)
    monkeypatch.setattr(sd, "download_benchmark",
                        lambda t, s, e, **k: flat(s, e) + 100.0)
    monkeypatch.setenv("ENGINE_DATABASE_URL", plane)
    monkeypatch.delenv("ENGINE_WARMUP_MONTHS", raising=False)
    strat = RankAndHold(spec)
    params = params or strat.get_default_parameters()
    loaded_from = loaded_from or warmup.load_start(
        start, warmup.months_for(strat, params, "ME"))
    shared = sd.build_shared_data(plane, start, LAST_BAR, "ME", market="CRYPTO",
                                  warmup_from=loaded_from)
    ret, tw = strat.generate_signals(shared, params)
    return warmup.cut(universe.fill_returns(ret, shared["delist_ret"]), tw, start)[1]


def test_weights_at_t_ignore_every_bar_after_t(world, tmp_path, monkeypatch):
    cut = "2020-06-30"
    scrambled = _plane(tmp_path / "scrambled.sqlite", _prices(5, perturb_after=cut))
    base, moved = _book(monkeypatch, world["plane"]), _book(monkeypatch, scrambled)
    assert base.index[0] == pd.Timestamp("2019-01-31")
    pd.testing.assert_frame_equal(base.loc[:cut], moved.loc[:cut])
    # And the scramble is real: the books part ways once it is visible.
    assert not base.loc[cut:].iloc[2:].equals(moved.loc[cut:].iloc[2:])


# ── How long the pre-roll is ──────────────────────────────────────────────────

def _months(name: str, freq: str, **params) -> int:
    strat = get_registry().get(name)
    return warmup.months_for(strat, {**strat.get_default_parameters(), **params}, freq)


def test_the_pre_roll_is_sized_from_the_spec(monkeypatch):
    monkeypatch.delenv("ENGINE_WARMUP_MONTHS", raising=False)
    # 12 + pad 2 periods; monthly, then quarterly inside the 40/40/20 blend.
    assert _months("MomSharpe", "ME") == 17
    assert _months("MomSharpe / IVVB11 / CDI (40/40/20)", "QE") == 45
    assert _months("MomSharpe", "ME", lookback=24) == 29
    # A hand-written strategy: no lookback to read, the generous default.
    code = next(n for n in sorted(get_registry().names())
                if not isinstance(get_registry().get(n), (RankAndHold, FixedWeight)))
    assert _months(code, "ME") == warmup.CODE_MONTHS + warmup.MARGIN_MONTHS
    # ...stretched by a look-back parameter on a coarse grid: QMV skips
    # lookback + 2 periods, which at QE is past the floor.
    assert _months("QMV", "QE") == 14 * 3 + 3
    assert _months("QMV", "QE", lookback=36) == 38 * 3 + 3


def test_expression_windows_count_in_their_own_units(monkeypatch):
    monkeypatch.delenv("ENGINE_WARMUP_MONTHS", raising=False)

    def months(expr, freq="ME", **sig):
        spec = {**MOM, "signal": {"expr": expr, **sig}, "warmup_pad": 0}
        return warmup.months_for(RankAndHold(spec), {}, freq)

    # Rows: what `ret` consumes (1) + the window's (47) + the i-1 read (1).
    assert months("-roll_std(ret, 48)") == 49 + 3
    # Windows nest by adding: 1 + 1 + 11 + 35 + 1 rows.
    assert months("roll_mean(roll_sum(shift(log_ret, 1), 12), 36)") == 49 + 3
    # Inside at_rebalance a window is sessions: 252 is a year, plus the i-1 row.
    assert months("-at_rebalance(roll_std(pct_change(adj_close), 252))") == 13 + 3
    # A scalar the expression reads, resolved like the run resolves it.
    assert months("roll_sum(ret, lookback * 4)", lookback=12) == 49 + 3
    # A bundle key costs its own warm-up: mf_composite is 13 rows before the
    # mean starts, dist_ma200 200 sessions — whether inside a window or bare.
    assert months("roll_mean(mf_composite, 12)") == 13 + 11 + 1 + 3
    assert months("roll_mean(dist_ma200, 12)") == math.ceil(1 + 200 / 21 + 11 + 1) + 3
    # The glitch mask is 12 periods of its own, read inside an expression or,
    # by default, applied over the whole signal.
    assert months("roll_mean(mask_glitch(ret), 3)", apply_glitch=False) == 12 + 2 + 1 + 3
    assert months("-roll_std(ret, 3)", apply_glitch=False) == 1 + 2 + 1 + 3
    assert months("-roll_std(ret, 3)") == 12 + 1 + 3
    bare = {**MOM, "signal": {"factor": "mf_composite"}, "warmup_pad": 0}
    assert warmup.months_for(RankAndHold(bare), {}, "ME") == 13 + 1 + 3
    # Bounded, whatever the expression asks for.
    assert months("roll_sum(ret, 600)") == warmup.MAX_MONTHS


def test_the_env_knob_overrides_every_strategy(monkeypatch):
    monkeypatch.setenv("ENGINE_WARMUP_MONTHS", "0")
    assert _months("MomSharpe", "ME") == 0
    assert warmup.load_start("2019-01-01", 0) == "2019-01-01"
    monkeypatch.setenv("ENGINE_WARMUP_MONTHS", "24")
    assert _months("MomSharpe / IVVB11 / CDI (40/40/20)", "QE") == 24
    monkeypatch.setenv("ENGINE_WARMUP_MONTHS", "two years")
    with pytest.raises(ValueError, match="ENGINE_WARMUP_MONTHS must be a whole number"):
        _months("MomSharpe", "ME")


# ── Review regressions ────────────────────────────────────────────────────────

def test_a_hold_row_on_the_first_rebalance_keeps_the_book_held_into_it(world):
    """The crash empties ThinMom's universe, so its rows after it are "hold"
    rows. A window opening on one used to start in cash — the simulator reads an
    all-zero first row as "not started" — and report a warm-up warning with five
    years of history loaded, while a longer run held its book through it."""
    short, s_curves = _run(world, "ThinMom", "2020-05-01")
    _, l_curves = _run(world, "ThinMom", "2018-01-01")
    assert short["invested_from"] == "2020-05-31"
    assert not [w for w in short["data_warnings"]
                if w["code"] in (warmup.WARNING, warmup.NOT_INVESTED)]
    # The first row buys the held book as drifted to that date, so from there on
    # the two runs hold the same thing and earn the same pre-tax returns.
    a = _period_returns(s_curves, "2020-05-31")
    b = _period_returns(l_curves, "2020-05-31")
    assert len(a) == 31 and a.index.equals(b.index)
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=0, atol=1e-12)


def test_a_bundle_key_inside_a_window_is_warmed_up_before_the_window(world):
    m, _ = _run(world, "SmoothMF", "2019-01-01")
    assert m["warmup_months"] == 13 + 11 + 1 + 3
    assert m["invested_from"] == "2019-01-31"
    assert not _warnings(m)


def _book_frame(dates, live_from):
    tw = pd.DataFrame(0.0, index=pd.DatetimeIndex(dates), columns=["A", "B"])
    tw.loc[tw.index >= pd.Timestamp(live_from), "A"] = 1.0
    return tw


def test_an_empty_first_row_with_history_behind_it_is_not_called_a_warm_up():
    """Only a pre-roll the archive could not cover is a warm-up problem. A book
    empty for a reason of its own — thin universe, a filter — says so instead."""
    dates = pd.date_range("2019-01-31", periods=6, freq="ME")
    tw = _book_frame(dates, "2019-03-31")
    covered = {"sessions": pd.date_range("2017-08-01", "2019-06-30")}
    invested, notes = warmup.late_books("X", tw, covered, "2017-08-01", 17)
    assert invested == "2019-03-31"
    assert [n["code"] for n in notes] == [warmup.NOT_INVESTED]
    short = {"sessions": pd.date_range("2018-06-01", "2019-06-30")}
    _, notes = warmup.late_books("X", tw, short, "2017-08-01", 17)
    assert [n["code"] for n in notes] == [warmup.WARNING]


def test_invested_from_follows_what_the_window_actually_holds():
    months = pd.date_range("2018-11-30", periods=6, freq="ME")
    covered = {"sessions": pd.date_range("2017-01-01", "2019-06-30")}
    ret = pd.DataFrame(0.0, index=months, columns=["A", "B"])
    # Held into the window: `cut` carries the book, so invested from the open.
    held = _book_frame(months, "2018-11-30")
    held.loc["2019-01-31"] = 0.0
    _, tw = warmup.cut(ret, held, "2019-01-01")
    assert warmup.late_books("X", tw, covered, "2017-01-01", 17) == ("2019-01-31", [])
    # Wiped out while held: there is nothing to carry, so not invested yet.
    wiped, idle = ret.copy(), held.copy()
    idle.loc["2018-12-31"] = 0.0
    wiped.loc["2018-12-31", "A"] = -1.0
    _, tw = warmup.cut(wiped, idle, "2019-01-01")
    assert warmup.late_books("X", tw, covered, "2017-01-01", 17)[0] == "2019-02-28"
    # A blend's sleeve empty on the open has its share in CDI on both runs: the
    # blend is not invested in it there, whatever the sleeve held before.
    blend = _book_frame(months, "2018-11-30")
    sleeves = {**covered, "_sleeve_books": {"S": held}}
    _, tw = warmup.cut(ret, blend, "2019-01-01")
    invested, notes = warmup.late_books("X", tw, sleeves, "2017-01-01", 17)
    assert invested == "2019-02-28"
    assert [(n["code"], n["detail"]["strategy"]) for n in notes] == \
        [(warmup.NOT_INVESTED, "S")]


def test_the_glitch_mask_is_warmed_up_before_the_window(tmp_path, monkeypatch):
    """`_build_signal` masks every signal with `has_glitch` (a 12-period rolling
    max) unless the spec opts out, and that mask is outside the expression. A
    pre-roll sized from the expression alone left BottomVol — six months of
    daily vol — ten rows: the mask had not started on the window's first rows,
    and the book held a name whose unadjusted 10:1 reverse split a longer run
    screens out for a year."""
    prices = _prices(5)
    split = (prices["symbol"] == "S03USDT") & (prices["date"] >= pd.Timestamp("2018-06-15").date())
    for col in ("open", "high", "low", "close", "adj_close", "split_adj_close",
                "close_brl", "adj_close_brl"):
        prices.loc[split, col] *= 10.0
    plane = _plane(tmp_path / "split.sqlite", prices)
    spec = {**get_registry().get("BottomVol").spec, "market": "CRYPTO"}
    strat = RankAndHold(spec)
    params = {**strat.get_default_parameters(), "min_adtv": 0, "min_price": 0}
    assert warmup.months_for(strat, params, "ME") == 13 + 3
    window = _book(monkeypatch, plane, spec, params)
    full = _book(monkeypatch, plane, spec, params, loaded_from=FIRST_BAR)
    assert window.loc["2019-01-31", "S03USDT"] == 0.0
    pd.testing.assert_frame_equal(window, full)


def test_cut_drifts_the_held_book_exactly_as_the_simulator_marks_it():
    dates = pd.date_range("2019-01-31", periods=4, freq="ME")
    tw = pd.DataFrame([[0.5, 0.5], [0.0, 0.0], [0.0, 0.0], [0.2, 0.8]],
                      index=dates, columns=["A", "B"])
    ret = pd.DataFrame([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]],
                       index=dates, columns=["A", "B"])
    r_in, tw_in = warmup.cut(ret, tw, "2019-03-01")
    assert r_in.index.equals(dates[2:])
    # A doubled in February and B in March: back to 50/50 by the March close.
    assert tw_in.iloc[0].tolist() == pytest.approx([0.5, 0.5])
    assert tw_in.iloc[1].tolist() == [0.2, 0.8]
    # Nothing held before the window: the empty first row stays empty.
    _, untouched = warmup.cut(ret, tw.iloc[1:], "2019-03-01")
    assert untouched.iloc[0].tolist() == [0.0, 0.0]
