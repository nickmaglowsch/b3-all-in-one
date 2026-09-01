"""A delisting has to cost the book what it actually cost — through the real
simulator, not through the loader alone.

Stopping the price fill at delisting (test_survivorship_filter.py) is only half
the fix. If the terminal return never reaches `run_simulation`, the position
simply stops moving and the fictional flat hold comes back one layer down. These
tests run the actual simulator and assert on the equity curve and the tax ledger.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtests.core import universe
from backtests.core.simulation import _apply_returns, run_simulation

DATES = pd.to_datetime(["2010-01-31", "2010-02-28", "2010-03-31", "2010-04-30"])


def _intervals(delisting=None, worthless=0):
    return pd.DataFrame([dict(
        ticker="DEAD", listing_date=pd.NaT,
        delisting_date=pd.Timestamp(delisting) if delisting else pd.NaT,
        is_worthless=worthless)])


def _book(delisting="2010-02-28", worthless=0):
    """Half DEAD, half LIVE, flat returns, DEAD delists after the second row."""
    ret = pd.DataFrame({"DEAD": [0.0] * 4, "LIVE": [0.0] * 4}, index=DATES)
    dr = universe.delisting_returns(
        _intervals(delisting, worthless), DATES, ["DEAD", "LIVE"])
    weights = pd.DataFrame({"DEAD": [0.5, 0.5, 0.0, 0.0],
                            "LIVE": [0.5, 0.5, 1.0, 1.0]}, index=DATES)
    return universe.fill_returns(ret, dr), weights


# ── the clip that used to eat the write-off ───────────────────────────────────

def test_a_total_loss_is_not_clipped_to_ninety_percent():
    """min_ret=-0.90 guards unadjusted-split artifacts. A bankruptcy is not an
    artifact, and clipping it handed the book a tenth of a wiped-out position
    back — forever, on every such event."""
    positions = {"X": {"current_value": 1000.0}}
    _apply_returns(positions, pd.Series({"X": -1.0}))
    assert positions["X"]["current_value"] == 0.0


def test_the_artifact_guard_still_applies_above_a_total_loss():
    positions = {"X": {"current_value": 1000.0}}
    _apply_returns(positions, pd.Series({"X": -0.99}))
    assert positions["X"]["current_value"] == pytest.approx(100.0)  # -90%


def test_the_upside_guard_is_untouched():
    positions = {"X": {"current_value": 100.0}}
    _apply_returns(positions, pd.Series({"X": 99.0}))
    assert positions["X"]["current_value"] == 600.0    # clipped at +500%


# ── through run_simulation ────────────────────────────────────────────────────

def test_a_worthless_delisting_costs_the_book_its_whole_weight():
    """Half the book in a name that goes to zero should cost about half the book.
    Under the old clip it cost 45% and the corpse kept paying."""
    ret, weights = _book(worthless=1)
    res = run_simulation(ret, weights, initial_capital=100_000.0, name="wipeout")
    final = res["pretax_values"].iloc[-1]
    assert 49_000 < final < 51_000


def test_a_liquidated_delisting_costs_nothing():
    """Leaving a venue is not going to zero. An acquisition, or a pair that
    trades on elsewhere, pays the last mark and the book is whole."""
    ret, weights = _book(worthless=0)
    res = run_simulation(ret, weights, initial_capital=100_000.0, name="liquidated")
    assert abs(res["pretax_values"].iloc[-1] - 100_000.0) < 1e-6


def test_the_dead_name_leaves_the_book():
    """After the write-off the position must be gone, not held at zero — a
    zero-value ghost still shows up in turnover and in the trade list."""
    ret, weights = _book(worthless=1)
    res = run_simulation(ret, weights, initial_capital=100_000.0, name="wipeout")
    assert res["pretax_values"].iloc[-1] > 0


def test_the_realised_loss_reaches_the_tax_ledger():
    """The exit is a real disposal. Its loss has to be available to offset a
    later gain, or the after-tax curve overstates the tax the holder owes."""
    ret = pd.DataFrame({"DEAD": [0.0, 0.0, 0.0, 0.0],
                        "LIVE": [0.0, 0.0, 1.0, 0.0]}, index=DATES)
    dr = universe.delisting_returns(
        _intervals("2010-02-28", 1), DATES, ["DEAD", "LIVE"])
    weights = pd.DataFrame({"DEAD": [0.5, 0.5, 0.0, 0.0],
                            "LIVE": [0.5, 0.5, 1.0, 1.0]}, index=DATES)
    res = run_simulation(universe.fill_returns(ret, dr), weights,
                         initial_capital=100_000.0, name="offset")
    assert res["loss_carryforward"].max() > 0


# ── the property the whole feature is for ─────────────────────────────────────

def test_survivorship_bias_makes_the_same_strategy_look_better():
    """The headline number. The same book, same weights, same returns — one run
    knows the name died, the other pretends it never existed. If dropping the
    dead name did not flatter the result, nothing here would be worth building.
    """
    ret, weights = _book(worthless=1)
    honest = run_simulation(ret, weights, initial_capital=100_000.0, name="honest")

    survivor = run_simulation(
        ret.drop(columns=["DEAD"]),
        pd.DataFrame({"LIVE": [1.0] * 4}, index=DATES),
        initial_capital=100_000.0, name="survivor")

    assert survivor["pretax_values"].iloc[-1] > honest["pretax_values"].iloc[-1]
