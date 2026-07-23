"""Dated cash-flow replay: run_simulation's Series contribution (what-if) works,
and the scalar contribution path is byte-identical to before the widening."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.core.simulation import run_simulation
from scripts.whatif_run import _build_flows

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
