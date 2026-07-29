"""Dated cash-flow replay: run_simulation's Series contribution (what-if) works,
and the scalar contribution path is byte-identical to before the widening."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.core.simulation import run_simulation
from scripts.run_backtest_json import _build_flows, _capital_and_flows

IDX = pd.date_range("2020-01-31", periods=5, freq="ME")
COLS = ["A", "B"]
HALF = [[0.5, 0.5]] * 5
FLAT = [[0.0, 0.0]] * 5


def _run(weights_rows, returns_rows, **kw):
    rm = pd.DataFrame(returns_rows, index=IDX[: len(returns_rows)], columns=COLS)
    tw = pd.DataFrame(weights_rows, index=rm.index, columns=COLS)
    kw.setdefault("tax_rate", 0.0)
    return run_simulation(rm, tw, **kw)


# ── Dated Series contribution (the what-if replay) ────────────────────────────

def test_dated_series_deploys_flows_at_next_rebalance():
    # Two real flows on non-rebalance dates: a Jan-15 buy (before the first live
    # mark) and a Mar-10 buy. Each lands on the next monthly rebalance. Nothing
    # is lost, and the final NAV (zero returns) equals the total money in.
    flows = pd.Series({pd.Timestamp("2020-01-15"): 1_000.0,
                       pd.Timestamp("2020-03-10"): 500.0})
    res = _run(HALF, FLAT, initial_capital=0.0, contribution=flows)
    # inception row deploys 0; Jan-15 flow lands Feb; Mar-10 flow lands Mar.
    assert res["contributions"].tolist() == pytest.approx([0.0, 1_000.0, 500.0, 0.0, 0.0])
    assert res["aftertax_values"].iloc[-1] == pytest.approx(1_500.0)
    assert res["contributions"].sum() == pytest.approx(1_500.0)   # no flow lost


def test_dated_series_flow_split_over_the_book():
    # The dated cash is spread by target weights like the scalar aporte: a 50/50
    # target puts half the deployed flow into each leg.
    flows = pd.Series({pd.Timestamp("2020-01-10"): 400.0})
    res = _run([[0.5, 0.5]] * 3, [[0.0, 0.0]] * 3, initial_capital=0.0,
               contribution=flows)
    assert res["aftertax_values"].iloc[-1] == pytest.approx(400.0)


def test_dated_series_needs_a_positive_flow():
    # initial_capital=0 with an all-non-positive flow series is "no money".
    flows = pd.Series({pd.Timestamp("2020-01-10"): 0.0})
    with pytest.raises(ValueError, match="No money"):
        _run(HALF, FLAT, initial_capital=0.0, contribution=flows)


def test_build_flows_sums_same_date_and_sorts():
    s = _build_flows([{"date": "2020-03-01", "amount": 5.0},
                      {"date": "2020-01-15", "amount": 10.0},
                      {"date": "2020-01-15", "amount": 2.0}])
    assert list(s.index) == [pd.Timestamp("2020-01-15"), pd.Timestamp("2020-03-01")]
    assert s.loc[pd.Timestamp("2020-01-15")] == pytest.approx(12.0)


# ── The one branch that separates a replay from a plain backtest ──────────────

def test_replay_config_zeroes_the_lump_sum_and_opens_at_the_first_flow():
    cfg = {"flows": [{"date": "2021-03-04", "amount": 700.0},
                     {"date": "2020-06-01", "amount": 300.0}]}
    start, capital, contribution = _capital_and_flows(
        cfg, {"start_date": "2005-01-01", "initial_capital": 100_000.0})
    assert start == "2020-06-01"        # the tenant's first trade, not the default
    assert capital == 0.0               # every cruzeiro comes from the flows
    assert contribution.sum() == pytest.approx(1_000.0)


def test_plain_backtest_config_keeps_the_strategy_defaults():
    params = {"start_date": "2010-01-01", "initial_capital": 50_000.0,
              "contribution": 250.0}
    assert _capital_and_flows({}, params) == ("2010-01-01", 50_000.0, 250.0)


def test_explicit_start_wins_over_the_first_flow():
    cfg = {"flows": [{"date": "2020-06-01", "amount": 300.0}], "start": "2019-01-01"}
    assert _capital_and_flows(cfg, {})[0] == "2019-01-01"


def test_empty_flows_list_is_an_error_not_a_silent_lump_sum_backtest():
    # A caller that asked for a replay and sent nothing must not get a
    # 100k lump-sum backtest back under the same name.
    with pytest.raises(SystemExit, match="non-empty 'flows'"):
        _capital_and_flows({"flows": []}, {"initial_capital": 100_000.0})


# ── Scalar path is unchanged by the widening ──────────────────────────────────

def test_scalar_contribution_path_unchanged():
    # Mirrors test_simulation.test_contribution_lands_in_nav exactly: the scalar
    # aporte still lands 4 deposits over 5 monthly rows and NAV is untouched.
    res = _run(HALF, FLAT, initial_capital=100.0, contribution=1_000.0)
    assert res["aftertax_values"].iloc[-1] == pytest.approx(100.0 + 4 * 1_000.0)
    assert res["contributions"].tolist() == [0.0] + [1_000.0] * 4
    assert res["invested"].iloc[-1] == pytest.approx(100.0 + 4 * 1_000.0)


def test_scalar_zero_contribution_unchanged():
    # No contribution: the default backtest path — pure lump-sum, no deposits.
    res = _run([[1.0, 0.0]] * 3, [[0.0, 0.0], [0.10, 0.0], [0.10, 0.0]],
               initial_capital=100.0, contribution=0.0)
    assert res["contributions"].tolist() == [0.0, 0.0, 0.0]
    assert np.allclose(res["aftertax_values"].to_numpy(), 100.0 * 1.1 ** np.arange(3))
