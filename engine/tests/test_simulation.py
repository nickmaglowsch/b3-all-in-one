"""Tests for the generic simulation engine (backtests/core/simulation.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import copy

from backtests.core.metrics import value_to_ret
from backtests.core import tax
from backtests.core.simulation import (
    run_simulation, _apply_returns, _execute_rebalance, compute_trades,
)

IDX = pd.date_range("2020-01-31", periods=5, freq="ME")
COLS = ["A", "B"]


def _run(weights_rows, returns_rows, **kw):
    rm = pd.DataFrame(returns_rows, index=IDX[: len(returns_rows)], columns=COLS)
    tw = pd.DataFrame(weights_rows, index=rm.index, columns=COLS)
    kw.setdefault("initial_capital", 100.0)
    kw.setdefault("tax_rate", 0.0)
    return run_simulation(rm, tw, **kw)


def test_zero_weight_row_after_inception_holds_positions():
    # Month 3 emits an all-zero row (e.g. <5 valid names that period) — the
    # engine must hold current positions, not liquidate to nothing (which
    # zeroed NAV and faked a margin call).
    tw_rows = [[1.0, 0.0], [1.0, 0.0], [0.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
    rets = [[0.0, 0.0], [0.10, 0.0], [0.10, 0.0], [0.10, 0.0], [0.10, 0.0]]
    res = _run(tw_rows, rets)
    vals = res["aftertax_values"]
    assert len(vals) == 5, "must not truncate with a bogus margin call"
    assert np.allclose(vals.values, 100.0 * 1.1 ** np.arange(5))
    assert res["turnover"].iloc[2] == 0.0


def test_underinvested_row_warns():
    # A live row summing < 1 must warn (its uninvested weight is dropped from NAV).
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _run([[0.5, 0.0], [0.5, 0.0]], [[0.0, 0.0], [0.0, 0.0]])
    assert "summing < 1" in buf.getvalue()


def test_fully_invested_row_does_not_warn():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _run([[0.6, 0.4], [0.6, 0.4]], [[0.0, 0.0], [0.0, 0.0]])
    assert "summing < 1" not in buf.getvalue()


def test_initial_deployment_charged_slippage():
    res = _run([[1.0, 0.0], [1.0, 0.0]], [[0.0, 0.0], [0.0, 0.0]], slippage=0.01)
    assert res["aftertax_values"].iloc[0] == pytest.approx(99.0)
    assert res["pretax_values"].iloc[0] == pytest.approx(99.0)


def test_excess_drag_deducted_from_nav():
    # Sell 10, buy 10, slippage 60% -> drag 12 exceeds buy liquidity 10.
    # Buys scale to zero and the residual 2 must come out of NAV (previously
    # it silently vanished).
    positions = {"A": {"cost_basis": 100.0, "current_value": 100.0}}
    tw = pd.Series({"A": 0.9, "B": 0.1})
    _execute_rebalance(
        positions, tw, 100.0, slippage=0.6, ledger=tax.free_ledger(),
        when=pd.Timestamp("2024-01-31"),
    )
    nav = sum(p["current_value"] for p in positions.values())
    assert nav == pytest.approx(90.0 - 2.0)


def test_apply_returns_allows_large_genuine_gains():
    pos = {"A": {"cost_basis": 10.0, "current_value": 10.0}}
    _apply_returns(pos, pd.Series({"A": 2.0}))  # +200% must not be clipped
    assert pos["A"]["current_value"] == pytest.approx(30.0)


# ── compute_trades: the pure rebalance diff (extracted from _execute_rebalance) ─


def test_compute_trades_long_rebalance():
    # A overweight -> sell down; B not held -> buy up to target.
    pos = {"A": {"cost_basis": 100.0, "current_value": 100.0}}
    sells, buys = compute_trades(pos, pd.Series({"A": 0.5, "B": 0.5}), 100.0)
    assert sells == pytest.approx({"A": 50.0})
    assert buys == pytest.approx({"B": 50.0})


def test_compute_trades_new_short_is_negative_buy_not_sell():
    # target_w < 0 on an unheld ticker: negative capital deployment, never a
    # taxed sell (you can't sell what you don't own).
    sells, buys = compute_trades({}, pd.Series({"S": -0.3}), 100.0)
    assert sells == {}
    assert buys == pytest.approx({"S": -30.0})


def test_compute_trades_cover_short_is_a_sell():
    # Currently short, diff > 0 (buying back toward 0) is routed to sells so the
    # tax engine fires on the covered P&L.
    pos = {"S": {"cost_basis": -100.0, "current_value": -80.0}}
    sells, buys = compute_trades(pos, pd.Series({"S": 0.0}), 100.0)
    assert sells == pytest.approx({"S": 80.0})   # diff = 0 - (-80)
    assert buys == {}


def test_compute_trades_increase_existing_short_is_a_negative_buy():
    # Deepening an existing short sells nothing: it is more negative capital,
    # exactly like opening one. It used to land in sells, where the sell loop
    # read it as a cover of the whole short.
    pos = {"S": {"cost_basis": -100.0, "current_value": -100.0}}
    sells, buys = compute_trades(pos, pd.Series({"S": -1.5}), 100.0)
    assert sells == {}
    assert buys == pytest.approx({"S": -50.0})   # -150 - (-100)


def test_rebalance_extending_short_keeps_the_short_and_nav():
    # Audit repro: NAV 1000 = L 1100 + S -100; target L 1.2 / S -0.2. The short
    # must grow to -200 and NAV stay 1000 — not vanish and lift NAV to 1200.
    pos = {"L": {"cost_basis": 1100.0, "current_value": 1100.0},
           "S": {"cost_basis": -100.0, "current_value": -100.0}}
    _execute_rebalance(pos, pd.Series({"L": 1.2, "S": -0.2}), 1000.0, 0.0,
                       tax.free_ledger(), pd.Timestamp("2020-01-31"))
    assert pos["S"]["current_value"] == pytest.approx(-200.0)
    assert pos["L"]["current_value"] == pytest.approx(1200.0)
    assert sum(p["current_value"] for p in pos.values()) == pytest.approx(1000.0)


def test_rebalance_drag_is_not_refunded_by_shrinking_a_short():
    # Same trade with 10% slippage: turnover 200 -> drag 20 must leave NAV.
    # Scaling the short down with the long buys gave the drag straight back.
    pos = {"L": {"cost_basis": 1100.0, "current_value": 1100.0},
           "S": {"cost_basis": -100.0, "current_value": -100.0}}
    _execute_rebalance(pos, pd.Series({"L": 1.2, "S": -0.2}), 1000.0, 0.1,
                       tax.free_ledger(), pd.Timestamp("2020-01-31"))
    assert pos["S"]["current_value"] == pytest.approx(-200.0)
    assert sum(p["current_value"] for p in pos.values()) == pytest.approx(980.0)


def test_compute_trades_flip_long_to_short_is_close_then_open():
    pos = {"A": {"cost_basis": 50.0, "current_value": 60.0}}
    sells, buys = compute_trades(pos, pd.Series({"A": -0.3}), 100.0)
    assert sells == pytest.approx({"A": 60.0})
    assert buys == pytest.approx({"A": -30.0})
    p = copy.deepcopy(pos)
    _execute_rebalance(p, pd.Series({"A": -0.3}), 100.0, 0.0,
                       tax.free_ledger(), pd.Timestamp("2020-01-31"))
    assert p["A"]["current_value"] == pytest.approx(-30.0)


def test_compute_trades_flip_short_to_long_is_cover_then_open():
    pos = {"A": {"cost_basis": -50.0, "current_value": -40.0}}
    sells, buys = compute_trades(pos, pd.Series({"A": 0.5}), 100.0)
    assert sells == pytest.approx({"A": 40.0})
    assert buys == pytest.approx({"A": 50.0})
    p = copy.deepcopy(pos)
    _execute_rebalance(p, pd.Series({"A": 0.5}), 100.0, 0.0,
                       tax.free_ledger(), pd.Timestamp("2020-01-31"))
    assert p["A"]["current_value"] == pytest.approx(50.0)


def test_compute_trades_nan_target_and_subcent_threshold():
    # NaN target -> 0 (sell the whole position); sub-1-cent diffs are ignored.
    pos = {"A": {"cost_basis": 100.0, "current_value": 100.0},
           "C": {"cost_basis": 50.0, "current_value": 50.0}}
    sells, buys = compute_trades(pos, pd.Series({"A": float("nan"), "C": 0.5}), 100.0)
    assert sells == pytest.approx({"A": 100.0})   # C already exactly on target
    assert buys == {}


def test_compute_trades_is_pure():
    pos = {"A": {"cost_basis": 100.0, "current_value": 100.0}}
    before = copy.deepcopy(pos)
    compute_trades(pos, pd.Series({"A": 0.5, "B": 0.5}), 100.0)
    assert pos == before   # no mutation


def test_compute_trades_turnover_matches_execute_rebalance():
    # Locks the extraction: turnover derived from the returned dicts equals the
    # turnover _execute_rebalance reports (it now calls compute_trades internally).
    pos = {"A": {"cost_basis": 100.0, "current_value": 120.0},
           "B": {"cost_basis": 40.0, "current_value": 30.0}}
    tw = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    nav = 150.0
    sells, buys = compute_trades(pos, tw, nav)
    turnover_cash = sum(sells.values()) + sum(abs(v) for v in buys.values())
    *_, turnover_pct = _execute_rebalance(
        copy.deepcopy(pos), tw, nav, slippage=0.0, ledger=tax.free_ledger(),
        when=pd.Timestamp("2024-01-31"),
    )
    assert turnover_pct * max(nav, 1.0) == pytest.approx(turnover_cash)


# ── Periodic buy-ins (aportes) ────────────────────────────────────────────────

FLAT = [[0.0, 0.0]] * 5
HALF = [[0.5, 0.5]] * 5


def test_contribution_lands_in_nav():
    # 5 monthly rows, zero returns: 4 deposits (none on the inception row).
    res = _run(HALF, FLAT, contribution=1_000.0)
    assert res["aftertax_values"].iloc[-1] == pytest.approx(100.0 + 4 * 1_000.0)
    assert res["contributions"].tolist() == [0.0] + [1_000.0] * 4
    assert res["invested"].iloc[-1] == pytest.approx(100.0 + 4 * 1_000.0)


def test_contribution_buys_more_of_the_underweight():
    # THE headline property: A doubled to 100 while B stayed at 50 against a
    # 50/50 target, so B is farther below target and must take more of the new
    # cash. Asserted per leg — NAV and turnover totals are identical for an
    # even 50/50 split, so they cannot discriminate a broken implementation.
    pos = {"A": {"cost_basis": 50.0, "current_value": 100.0},
           "B": {"cost_basis": 50.0, "current_value": 50.0}}
    before = {t: p["current_value"] for t, p in pos.items()}
    _execute_rebalance(
        pos, pd.Series({"A": 0.5, "B": 0.5}), 150.0 + 100.0,   # NAV + buy-in
        slippage=0.0, ledger=tax.free_ledger(),
        when=pd.Timestamp("2024-01-31"),
    )
    bought = {t: pos[t]["current_value"] - before[t] for t in pos}
    assert bought["B"] == pytest.approx(75.0)
    assert bought["A"] == pytest.approx(25.0)
    assert bought["B"] == pytest.approx(3 * bought["A"])   # not an even split

    # And the same through the full loop: the deposit reaches the book.
    res = run_simulation(
        pd.DataFrame([[0.0, 0.0], [1.0, 0.0]], index=IDX[:2], columns=COLS),
        pd.DataFrame([[0.5, 0.5], [0.5, 0.5]], index=IDX[:2], columns=COLS),
        initial_capital=100.0, tax_rate=0.0, contribution=100.0,
    )
    assert res["aftertax_values"].iloc[-1] == pytest.approx(250.0)


def test_withdrawal_sells_and_is_taxed():
    # Negative contribution = withdrawal: it must come out of NAV and the forced
    # sells must go through the tax engine (not silently escape it).
    rets = [[0.0, 0.0], [1.0, 0.0]]        # A doubles -> selling realizes a gain
    tw = pd.DataFrame([[0.5, 0.5], [0.5, 0.5]], index=IDX[:2], columns=COLS)
    res = run_simulation(
        pd.DataFrame(rets, index=IDX[:2], columns=COLS), tw,
        initial_capital=100.0, tax_rate=0.15, contribution=-30.0,
    )
    assert res["contributions"].iloc[-1] == pytest.approx(-30.0)
    assert res["invested"].iloc[-1] == pytest.approx(70.0)
    assert res["tax_paid"].iloc[-1] > 0          # the forced sell was taxed
    # NAV 150, withdraw 30 -> 120 before tax, minus the tax on the realized gain.
    assert res["aftertax_values"].iloc[-1] == pytest.approx(120.0 - res["tax_paid"].iloc[-1])


def test_contributions_reduce_realized_tax():
    # The economic reason DCA beats lump sum after tax: new cash does the
    # rebalancing, so less has to be sold and less gain is realized.
    rets = [[0.0, 0.0]] + [[0.30, 0.0]] * 4      # A keeps running away from target
    kw = dict(tax_rate=0.15, slippage=0.001)
    lump = _run(HALF, rets, **kw)
    dca = _run(HALF, rets, contribution=50.0, **kw)
    assert dca["tax_paid"].sum() < lump["tax_paid"].sum()


def test_weekly_cadence_deposits_once_per_month():
    # W-FRI: the buy-in lands on the first weekly rebalance of each new month.
    wk = pd.date_range("2020-01-03", periods=10, freq="W-FRI")   # Jan x5, Feb x4, Mar
    res = run_simulation(
        pd.DataFrame(0.0, index=wk, columns=COLS),
        pd.DataFrame(0.5, index=wk, columns=COLS),
        initial_capital=100.0, tax_rate=0.0, contribution=1_000.0,
    )
    months = pd.DatetimeIndex(res["contributions"].index).to_period("M")
    paid = res["contributions"] > 0
    assert paid.sum() == 2                                   # Feb and Mar
    assert not months[paid].duplicated().any()               # never twice in a month
    assert res["contributions"][paid].eq(1_000.0).all()


def test_pure_dca_from_zero_capital():
    # initial_capital=0: no lump sum at all, the book is built by buy-ins alone.
    res = _run(HALF, FLAT, initial_capital=0.0, contribution=1_000.0)
    assert res["aftertax_values"].tolist() == pytest.approx([0.0, 1_000.0, 2_000.0, 3_000.0, 4_000.0])
    assert res["invested"].iloc[-1] == pytest.approx(4_000.0)


def test_no_money_at_all_is_rejected():
    with pytest.raises(ValueError, match="No money"):
        _run(HALF, FLAT, initial_capital=0.0, contribution=0.0)


def test_contribution_is_time_weighted_neutral():
    # Same weights and returns; deposits must not show up as performance.
    rets = [[0.02, 0.01], [-0.03, 0.01], [0.05, 0.01], [0.0, 0.01], [0.01, 0.01]]
    base = _run(HALF, rets)
    dca = _run(HALF, rets, contribution=1_000.0)
    r_base = value_to_ret(base["aftertax_values"])
    r_dca = value_to_ret(dca["aftertax_values"], dca["contributions"])
    assert np.allclose(r_base.values, r_dca.values, atol=1e-9)
    # ... and the naive pct_change would have lied (deposits dwarf the returns).
    assert value_to_ret(dca["aftertax_values"]).iloc[1] > 1.0


def test_contribution_accrues_by_calendar_month():
    # Quarterly grid -> each rebalance collects 3 months of buy-ins.
    q = pd.date_range("2020-03-31", periods=3, freq="QE")
    res = run_simulation(
        pd.DataFrame(0.0, index=q, columns=COLS),
        pd.DataFrame(0.5, index=q, columns=COLS),
        initial_capital=100.0, tax_rate=0.0, contribution=1_000.0,
    )
    assert res["contributions"].tolist() == [0.0, 3_000.0, 3_000.0]


def test_contribution_defers_over_hold_rows():
    # Month 3 has no valid targets (all-zero row): nothing is deposited that
    # month, and the next live rebalance collects both months.
    tw = [[0.5, 0.5], [0.5, 0.5], [0.0, 0.0], [0.5, 0.5], [0.5, 0.5]]
    res = _run(tw, FLAT, contribution=1_000.0)
    assert res["contributions"].tolist() == [0.0, 1_000.0, 0.0, 2_000.0, 1_000.0]
    assert res["aftertax_values"].iloc[-1] == pytest.approx(100.0 + 4 * 1_000.0)
