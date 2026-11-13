"""rebalance_plan: how a target with US / crypto sleeves splits into the B3 book's
own target and an aporte-funded off-B3 part. Pure — no database, no engine."""
from __future__ import annotations

import pytest

from platform_app import rebalance_plan as rp

OFF = {"SPY": "US", "BTCUSDT": "CRYPTO"}


def test_without_off_b3_sleeves_the_target_is_the_b3_books_own():
    split = rp.split_target({"PETR4": 0.6}, 0.4, {})
    assert (split.b3, split.cash, split.off, split.hold_b3) == ({"PETR4": 0.6}, 0.4, {}, False)
    assert split.aporte(1000) == (1000, 0)


def test_off_b3_weight_is_held_outside_and_the_b3_book_scaled_to_itself():
    split = rp.split_target({"PETR4": 0.5, "SPY": 0.2, "BTCUSDT": 0.1}, 0.2, OFF)
    assert split.b3 == {"PETR4": pytest.approx(0.5 / 0.7)}
    assert split.cash == pytest.approx(0.2 / 0.7)
    assert split.off == {"SPY": 0.2, "BTCUSDT": 0.1}
    assert split.aporte(1000) == pytest.approx((700, 300))
    assert split.aporte(0) == (0, 0)


def test_all_off_b3_holds_the_b3_book():
    split = rp.split_target({"SPY": 1.0}, 0.0, OFF)
    assert split.hold_b3 and split.b3 == {} and split.aporte(500) == (0, 500)


def test_drift_lists_off_b3_sleeves_with_no_current_position():
    split = rp.split_target({"PETR4": 0.7, "SPY": 0.3}, 0.0, OFF)
    rows = rp.drift_rows(split, {"PETR4": 1.0}, 4000.0, OFF)
    assert rows[0] == {"ticker": "PETR4", "target_w": 1.0, "current_w": 1.0,
                       "drift_w": 0.0, "drift_brl": 0.0}
    assert rows[1]["outside_b3"] and rows[1]["current_w"] is None
    # Holding the B3 book: its names show no drift, not a sell-everything.
    held = rp.drift_rows(rp.split_target({"SPY": 1.0}, 0.0, OFF), {"PETR4": 1.0}, 4000.0, OFF)
    assert held[0]["drift_w"] == 0.0


def _buy(t, brl, price):
    return {"ticker": t, "qty": brl // price, "est_price": price, "est_brl": brl}


def test_cap_buys_trims_the_last_and_names_every_buy_the_budget_ran_out_before():
    buys = [_buy("IVVB11", 2100, 350), _buy("AAPL34", 1045.5, 61.5), _buy("PETR4", 400, 40)]
    kept, unfunded = rp.cap_buys(buys, 400)
    # R$400: one IVVB11, then R$50 buys no AAPL34 (R$61.5) but one PETR4.
    assert [(b["ticker"], b["qty"]) for b in kept] == [("IVVB11", 1), ("PETR4", 1)]
    assert unfunded == ["AAPL34"]
    # R$350: one IVVB11 spends it all; both others come after the budget hit 0.
    assert rp.cap_buys(buys, 350)[1] == ["AAPL34", "PETR4"]
    assert rp.cap_buys(buys, 10_000) == (sorted(buys, key=lambda b: -b["est_brl"]), [])


def test_plan_notes_put_the_funding_rule_first_and_tell_their_cases_apart():
    split = rp.split_target({"PETR4": 0.7, "SPY": 0.3}, 0.0, OFF)
    notes = rp.plan_notes(split, OFF, 1234.5, {
        "buys": [{"ticker": "SPY"}], "unpriced_skipped": ["XPTO3"],
        "below_one_lot": ["BTCUSDT"]}, ["AAPL34"])
    assert notes[0].startswith("Ativos fora da B3 (SPY (EUA)) entram só com aporte")
    assert "R$ 370,35" in notes[0] and "(30%" in notes[0]
    assert any("14.754" in n for n in notes)
    assert any(n.startswith("sem cotação hoje") and "XPTO3" in n for n in notes)
    assert any("não chega a uma unidade de: BTCUSDT" in n for n in notes)
    assert any("o aporte acabou antes de: AAPL34" in n for n in notes)
    assert "Sem aporte" in rp.plan_notes(split, OFF, 0, {}, [])[0]
    assert rp.plan_notes(rp.split_target({"PETR4": 1.0}, 0.0, {}), {}, 0, {}, []) == []
