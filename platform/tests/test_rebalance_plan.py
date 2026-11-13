"""rebalance_plan: how a target with US / crypto sleeves splits into the B3 book's
own target and an aporte-funded off-B3 part. Pure — no database, no engine."""
from __future__ import annotations

import pytest

from platform_app import rebalance_plan as rp

OFF = {"SPY": "US", "BTCUSDT": "CRYPTO"}


def test_without_off_b3_sleeves_the_target_is_the_b3_books_own():
    split = rp.split_target({"PETR4": 0.6}, 0.4, {}, 0.6)
    assert split.b3 == {"PETR4": pytest.approx(0.6)} and split.cash == pytest.approx(0.4)
    assert (split.off, split.hold_b3) == ({}, False)
    assert split.aporte(1000) == (1000, 0, 0)


def test_off_b3_weight_is_held_outside_and_the_b3_book_scaled_to_itself():
    split = rp.split_target({"PETR4": 0.5, "SPY": 0.2, "BTCUSDT": 0.1}, 0.2, OFF, 0.5)
    assert split.b3 == {"PETR4": pytest.approx(0.5 / 0.7)}
    assert split.cash == pytest.approx(0.2 / 0.7)
    assert split.off == {"SPY": 0.2, "BTCUSDT": 0.1}
    assert split.aporte(1000) == pytest.approx((700, 300, 0))
    assert split.aporte(0) == (0, 0, 0)
    # Excluded or pinned, an off-B3 sleeve's share is kept as cash, not re-spread.
    assert split.aporte(1000, {"SPY"}) == pytest.approx((700, 100, 200))


def test_a_strategy_keeping_part_of_its_sleeve_in_cash_is_not_scaled_up():
    """The B3 book's share is its sleeves' 50%, not the 40% a strategy invests."""
    split = rp.split_target({"PETR4": 0.4, "SPY": 0.5}, 0.0, OFF, 0.5)
    assert split.b3 == {"PETR4": pytest.approx(0.8)}


@pytest.mark.parametrize("combined, cash", [
    ({"SPY": 1.0}, 0.0),
    ({"SPY": 0.99995}, 0.0),                  # a save within ±0.01 of 100
    ({"SPY": 0.50004, "BTCUSDT": 0.4999}, 0.0),
    ({"SPY": 0.9}, 0.1),                      # CASH_CDI is not a B3 asset sleeve
    ({"SPY": 0.9999}, 0.0001),
])
def test_no_b3_asset_sleeve_holds_the_b3_book_whatever_the_rounding(combined, cash):
    split = rp.split_target(combined, cash, OFF, 0.0)
    assert split.hold_b3 and split.b3 == {}
    # The aporte still goes abroad by weight; the CASH_CDI share stays cash.
    b3, buy, kept = split.aporte(1000)
    assert b3 + buy == pytest.approx(1000) and kept == 0
    assert b3 == pytest.approx(1000 * cash / (cash + sum(combined.values())))


def test_zero_percent_sleeves_are_in_neither_half():
    split = rp.split_target({"SPY": 0.0, "PETR4": 1.0}, 0.0, OFF, 1.0)
    assert split.off == {} and split.b3 == {"PETR4": 1.0} and not split.hold_b3


def test_drift_lists_off_b3_sleeves_with_no_current_position():
    split = rp.split_target({"PETR4": 0.7, "SPY": 0.3}, 0.0, OFF, 0.7)
    rows = rp.drift_rows(split, {"PETR4": 1.0}, 4000.0, OFF)
    assert rows[0] == {"ticker": "PETR4", "target_w": pytest.approx(1.0), "current_w": 1.0,
                       "drift_w": pytest.approx(0.0), "drift_brl": pytest.approx(0.0)}
    assert rows[1]["outside_b3"] and rows[1]["current_w"] is None
    # Holding the B3 book: its names show no drift, not a sell-everything.
    held = rp.drift_rows(rp.split_target({"SPY": 1.0}, 0.0, OFF, 0.0), {"PETR4": 1.0},
                         4000.0, OFF)
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
    split = rp.split_target({"PETR4": 0.7, "SPY": 0.3}, 0.0, OFF, 0.7)
    notes = rp.plan_notes(split, OFF, 1234.5, {
        "buys": [{"ticker": "SPY"}], "unpriced_skipped": ["XPTO3"],
        "below_one_lot": ["BTCUSDT"]}, ["AAPL34"])
    assert notes[0].startswith("Ativos fora da B3 (SPY (EUA)) entram só com aporte")
    assert "R$ 370,35 vão para SPY (EUA) (30%" in notes[0]
    assert any("14.754" in n for n in notes)
    assert any(n.startswith("sem cotação hoje") and "XPTO3" in n for n in notes)
    assert any("não chega a uma unidade de: BTCUSDT" in n for n in notes)
    assert any("o aporte acabou antes de: AAPL34" in n for n in notes)
    assert "Sem aporte" in rp.plan_notes(split, OFF, 0, {}, [])[0]
    assert rp.plan_notes(rp.split_target({"PETR4": 1.0}, 0.0, {}, 1.0), {}, 0, {}, []) == []


def test_an_excluded_off_b3_sleeve_is_said_to_stay_in_cash():
    split = rp.split_target({"PETR4": 0.7, "SPY": 0.2, "BTCUSDT": 0.1}, 0.0, OFF, 0.7)
    note = rp.funding_note(split, OFF, 3000, {"SPY"})
    assert "R$ 300,00 vão para BTCUSDT (cripto) (10%" in note
    assert "A parte de SPY (EUA) (R$ 600,00), excluída ou fixada neste plano, fica em caixa" in note
    # All of them excluded: nothing is said to go abroad.
    assert "vão para" not in rp.funding_note(split, OFF, 3000, {"SPY", "BTCUSDT"})


def test_a_held_b3_book_is_said_so():
    split = rp.split_target({"SPY": 0.9}, 0.1, OFF, 0.0)
    notes = rp.plan_notes(split, OFF, 0, {}, [])
    assert rp.HOLD_NOTE in notes
    # And the funding note does not promise a rebalance of the B3 book.
    assert "rebalanceada" not in notes[0]
