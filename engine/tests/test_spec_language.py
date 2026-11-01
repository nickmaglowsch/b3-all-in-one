"""The widened rank_and_hold language: signal legs and vars, weight_expr,
max_weight, sector filters and sector-neutral functions, dividend frames and
rebalance_months — on synthetic data, plus their save-time validation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.core import dividends, signal_dsl
from backtests.core.config_strategy import FixedWeight, RankAndHold
from backtests.core.spec_signal import only_in_months, resolve_factor
from backtests.core.warmup_size import strategy_months
from scripts.validate_spec import validate
from tests.test_config_strategy import _registry_with, _synthetic_shared


def _rank(**over) -> dict:
    spec = {"kind": "rank_and_hold", "name": "t", "rebalance": "ME",
            "universe": {"min_adtv": 0, "min_price": 0},
            "signal": {"expr": "roll_sum(shift(log_ret, 1), lookback)", "lookback": 3},
            "selection": {"top_n": 5, "min_names": 1}}
    return {**spec, **over}


def _held(tw: pd.DataFrame) -> pd.DataFrame:
    return tw[tw.abs().sum(axis=1) > 0]


def _sectors(cols) -> pd.DataFrame:
    # Half the names banks, half utilities, one with no sector on file.
    return pd.DataFrame({
        "ticker": cols[:-1],
        "ref_date": pd.Timestamp("1900-01-01"),
        "sector": ["Bancos" if i % 2 == 0 else "Energia Elétrica"
                   for i in range(len(cols) - 1)],
    })


# ── legs and vars ─────────────────────────────────────────────────────────────

def test_legs_rank_and_blend_like_the_equivalent_expression():
    shared = _synthetic_shared()
    legs = {"legs": [{"expr": "roll_sum(shift(log_ret, 1), lookback)", "weight": 0.7},
                     {"expr": "-roll_std(ret, lookback)", "weight": 0.3}],
            "lookback": 6, "apply_glitch": False}
    expr = {"expr": "0.7 * rank(roll_sum(shift(log_ret, 1), lookback))"
                    " + 0.3 * rank(-roll_std(ret, lookback))",
            "lookback": 6, "apply_glitch": False}
    _, a = RankAndHold(_rank(signal=legs)).generate_signals(shared, {})
    _, b = RankAndHold(_rank(signal=expr)).generate_signals(shared, {})
    pd.testing.assert_frame_equal(a, b)


def test_coverage_any_scores_a_name_on_the_legs_it_has():
    idx = pd.date_range("2020-01-31", periods=2, freq="ME")
    a = pd.DataFrame([[1.0, 2.0, 3.0]] * 2, index=idx, columns=list("XYZ"))
    b = pd.DataFrame([[3.0, np.nan, 1.0]] * 2, index=idx, columns=list("XYZ"))
    cfg = {"legs": [{"factor": "a", "transform": "raw"},
                    {"factor": "b", "transform": "raw"}]}
    every = resolve_factor(cfg, {"a": a, "b": b})
    some = resolve_factor({**cfg, "coverage": "any"}, {"a": a, "b": b})
    assert np.isnan(every.loc[idx[0], "Y"])          # all: Y lacks leg b
    assert some.loc[idx[0], "Y"] == 2.0              # any: Y scored on leg a alone
    assert some.loc[idx[0], "X"] == 2.0              # mean of 1 and 3


def test_vars_name_a_frame_the_expression_and_legs_can_read():
    shared = _synthetic_shared()
    inline = {"expr": "rank(roll_sum(shift(log_ret, 1), lookback))", "lookback": 4}
    named = {"vars": {"mom": "roll_sum(shift(log_ret, 1), lookback)",
                      "mom_r": "rank(mom)"},
             "expr": "mom_r", "lookback": 4}
    _, a = RankAndHold(_rank(signal=inline)).generate_signals(shared, {})
    _, b = RankAndHold(_rank(signal=named)).generate_signals(shared, {})
    pd.testing.assert_frame_equal(a, b)
    as_leg = {"vars": named["vars"], "legs": [{"factor": "mom_r", "transform": "raw"}],
              "lookback": 4}
    _, c = RankAndHold(_rank(signal=as_leg)).generate_signals(shared, {})
    pd.testing.assert_frame_equal(a, c)


# ── weights ───────────────────────────────────────────────────────────────────

def test_weight_expr_weights_the_held_names_proportionally():
    shared = _synthetic_shared()
    spec = _rank(weighting="expr", weight_expr="adtv")
    _, tw = RankAndHold(spec).generate_signals(shared, {})
    row = _held(tw).iloc[-1]
    i = tw.index.get_loc(row.name)
    held = row[row > 0]
    size = shared["adtv"].iloc[i - 1][held.index]
    assert np.allclose(held, size / size.sum())


def test_weight_expr_falls_back_to_equal_when_nothing_scores_above_zero():
    shared = _synthetic_shared()
    _, tw = RankAndHold(_rank(weighting="expr", weight_expr="-adtv")).generate_signals(
        shared, {})
    row = _held(tw).iloc[-1]
    assert np.allclose(row[row > 0], 0.2)


def test_max_weight_caps_each_name_and_keeps_the_book_whole():
    shared = _synthetic_shared()
    spec = _rank(weighting="expr", weight_expr="adtv ** 4", max_weight=0.25)
    _, tw = RankAndHold(spec).generate_signals(shared, {})
    held = _held(tw)
    assert held.max(axis=1).max() <= 0.25 + 1e-9
    assert np.allclose(held.sum(axis=1), 1.0)


# ── sectors ───────────────────────────────────────────────────────────────────

def test_sector_include_and_exclude_filter_the_universe():
    shared = _synthetic_shared()
    cols = list(shared["ret"].columns)
    shared["sector_pit"] = _sectors(cols)
    banks = {c for i, c in enumerate(cols[:-1]) if i % 2 == 0}
    for sectors, allowed in (({"include": ["banc"]}, banks),
                             ({"exclude": ["banc"]}, set(cols) - banks)):
        spec = _rank(universe={"min_adtv": 0, "min_price": 0, "sectors": sectors})
        _, tw = RankAndHold(spec).generate_signals(shared, {})
        held = set(tw.columns[(tw > 0).any()])
        assert held and held <= allowed


def test_sectors_without_sector_data_fail_loudly_not_silently_flat():
    shared = _synthetic_shared()
    shared["sector_pit"] = pd.DataFrame(columns=["ticker", "ref_date", "sector"])
    spec = _rank(universe={"sectors": {"include": ["banc"]}})
    with pytest.raises(ValueError, match="sector"):
        RankAndHold(spec).generate_signals(shared, {})


def test_sector_rank_ranks_within_each_sector():
    shared = _synthetic_shared()
    cols = list(shared["ret"].columns)
    shared["sector_pit"] = _sectors(cols)
    out = signal_dsl.evaluate("sector_rank(adtv)", shared)
    row, adtv = out.iloc[5], shared["adtv"].iloc[5]
    banks = [c for i, c in enumerate(cols[:-1]) if i % 2 == 0]
    assert np.allclose(row[banks], adtv[banks].rank(pct=True))
    assert np.isnan(row[cols[-1]])                   # no sector on file
    assert set(signal_dsl.FUNCTION_NAMES) >= {"sector_rank", "sector_zscore",
                                              "sector_demean"}


# ── dividends ─────────────────────────────────────────────────────────────────

def test_dividend_frames_trail_twelve_months_and_count_paying_years():
    grid = pd.date_range("2020-01-31", periods=48, freq="ME")
    raw = pd.DataFrame({"A": 10.0, "B": 20.0}, index=grid)
    raw.loc["2022-01-31":, "A"] = 5.0                # a 2:1 split
    events = pd.DataFrame({
        "ticker": ["A", "A", "A", "B"],
        "event_date": pd.to_datetime(["2020-03-10", "2021-03-10", "2022-03-10",
                                      "2020-06-15"]),
        "value": [1.0, 1.0, 0.5, 2.0],
    })
    f = dividends.frames(events, raw)
    t = pd.Timestamp("2022-06-30")
    assert f["div_yield_12m"].loc[t, "A"] == pytest.approx(0.1)   # 0.5 / 5, split-proof
    assert f["div_count_12m"].loc[t, "A"] == 1
    assert f["div_years_3y"].loc[t, "A"] == 3
    assert f["div_years_3y"].loc[t, "B"] == 1                     # one payment, in 2020
    assert f["div_yield_12m"].loc[pd.Timestamp("2020-02-29"), "A"] == 0


# ── rebalance_months ──────────────────────────────────────────────────────────

def test_rebalance_months_trades_on_the_calendar_and_holds_between():
    shared = _synthetic_shared()
    _, tw = RankAndHold(_rank(rebalance_months=[6, 12])).generate_signals(shared, {})
    assert set(_held(tw).index.month) == {6, 12}


def test_rebalance_months_on_a_weekly_grid_takes_the_first_week():
    idx = pd.date_range("2020-01-03", periods=30, freq="W-FRI")
    tw = only_in_months(pd.DataFrame(1.0, index=idx, columns=["A"]), [3])
    assert list(_held(tw).index) == [pd.Timestamp("2020-03-06")]


def test_a_nested_sleeve_on_rebalance_months_holds_its_book_in_the_blend(monkeypatch):
    shared = _synthetic_shared()
    shared["cdi_monthly"] = pd.Series(0.01, index=shared["ret"].index)
    inner = RankAndHold(_rank(name="Semi", rebalance_months=[6, 12]))
    _registry_with(monkeypatch, inner)
    blend = FixedWeight({"kind": "fixed_weight", "name": "b", "rebalance": "ME",
                         "sleeves": [{"strategy": "Semi", "weight": 0.5},
                                     {"asset": "CDI", "weight": 0.5}]})
    _, tw = blend.generate_signals(shared, {})
    first = tw.index[(tw.drop(columns="CDI_ASSET") > 0).any(axis=1)][0]
    assert first.month == 6                          # the sleeve's first book
    assert np.allclose(tw.loc[first:, "CDI_ASSET"], 0.5)   # its half never parks


# ── warm-up and validation ────────────────────────────────────────────────────

def test_warmup_counts_vars_legs_and_weight_expr():
    short = RankAndHold(_rank(signal={"expr": "ret", "apply_glitch": False}))
    via_var = RankAndHold(_rank(signal={"vars": {"slow": "roll_mean(ret, 24)"},
                                        "expr": "slow", "apply_glitch": False}))
    via_leg = RankAndHold(_rank(signal={"legs": [{"expr": "ret"},
                                                 {"expr": "roll_mean(ret, 24)"}],
                                        "apply_glitch": False}))
    via_w = RankAndHold(_rank(signal={"expr": "ret", "apply_glitch": False},
                              weighting="expr", weight_expr="roll_mean(adtv, 24)"))
    assert strategy_months(short, {}, "ME") == 2      # ret, read at i - 1
    for strat in (via_var, via_leg, via_w):           # a 24-row window, at least
        assert strategy_months(strat, {}, "ME") >= 24


def test_validator_accepts_the_new_shapes():
    spec = _rank(signal={"vars": {"mom": "roll_sum(shift(log_ret, 1), 6)"},
                         "legs": [{"factor": "mom"},
                                  {"factor": "div_yield_12m", "transform": "zscore"},
                                  {"expr": "sector_rank(-vol_60d)", "weight": 0.5}],
                         "coverage": "any"},
                 weighting="expr", weight_expr="1 / vol_60d",
                 universe={"sectors": {"exclude": ["banc"]}})
    assert validate(spec) == {"ok": True, "errors": [], "needs_fundamentals": False}


@pytest.mark.parametrize("spec, msg", [
    (_rank(signal={"vars": {"rank": "ret"}, "expr": "rank"}), "not a function"),
    (_rank(signal={"legs": [{"expr": "ret", "factor": "momentum"}]}), "exactly one"),
    (_rank(signal={"legs": [{"expr": "ret", "transform": "log"}]}), "transform"),
    (_rank(weighting="expr"), "weight_expr"),
    (_rank(weighting="expr", weight_expr="bogus(adtv)"), "unknown function"),
    (_rank(market="US", signal={"factor": "div_yield_12m"}), "B3 only"),
    (_rank(market="US", weight_expr="sector_rank(adtv)", weighting="expr"), "B3 only"),
    (_rank(market="CRYPTO", universe={"sectors": {"include": ["x"]}}), "B3 only"),
])
def test_validator_rejects(spec, msg):
    out = validate(spec)
    assert not out["ok"] and msg in " ".join(out["errors"])
