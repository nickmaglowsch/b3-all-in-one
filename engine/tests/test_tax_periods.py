"""The apuração period, and the three rules that differ between the markets.

Pure and offline. `taxcore/scripts/apuracao.py` is the authority these transcribe
and `test_markets.py` is the seam that catches a divergence in the constants;
what is checked here is the behaviour those constants are supposed to produce.
"""
from __future__ import annotations

import pandas as pd
import pytest
from backtests.core import markets, tax

JAN, FEB, NOV = (pd.Timestamp("2026-01-31"), pd.Timestamp("2026-02-28"),
                 pd.Timestamp("2026-11-30"))


def _book(cost: float, value: float, ticker: str = "A") -> dict:
    return {ticker: {"cost_basis": cost, "current_value": value}}


def _acoes(**kw) -> tax.Ledger:
    return tax.Ledger(tax.Rule(rate=0.15, exemption=20_000.0, **kw))


# ── The month is the unit, not the rebalance row ──────────────────────────────

def test_two_sales_in_one_month_are_measured_against_one_ceiling():
    """The isenção is a threshold on a calendar month's gross sales. Applied per
    rebalance row, a weekly book could sell R$19k every Friday and never pay."""
    ledger = _acoes()
    for day in ("2026-01-08", "2026-01-15"):
        # 15k of sales each, on a position bought at half its value.
        assert ledger.accrue(pd.Timestamp(day), {"A": 15_000.0},
                             _book(15_000.0, 30_000.0)) == 0.0
    # 30k of gross sales in January is over the ceiling, so the month is taxed.
    assert ledger.close() > 0


def test_a_month_under_the_ceiling_pays_nothing():
    ledger = _acoes()
    ledger.accrue(pd.Timestamp("2026-01-08"), {"A": 10_000.0},
                  _book(5_000.0, 20_000.0))
    assert ledger.close() == 0.0


def test_the_bill_lands_on_the_first_rebalance_of_the_next_period():
    """A period cannot be settled until it is over, and a DARF for month M is
    due at the end of M+1 anyway."""
    ledger = _acoes()
    assert ledger.accrue(JAN, {"A": 30_000.0}, _book(10_000.0, 30_000.0)) == 0.0
    due = ledger.accrue(FEB, {}, {})
    assert due == pytest.approx(0.15 * 20_000.0)


# ── The isento loss is no longer dropped ──────────────────────────────────────

def test_a_loss_in_an_isento_month_still_feeds_the_carry():
    """The old code returned early on the exempt branch and never touched the
    carryforward, so an isento loss vanished — quietly overstating the tax on
    the next gain. taxcore does `elif pnl < 0 and regra.carry: carry += -pnl`."""
    ledger = _acoes()
    ledger.accrue(JAN, {"A": 5_000.0}, _book(9_000.0, 5_000.0))   # -4k, isento
    ledger.accrue(FEB, {}, {})
    assert ledger.carry == pytest.approx(4_000.0)


def test_a_gain_in_an_isento_month_does_not_consume_the_carry():
    ledger = _acoes()
    ledger.accrue(JAN, {"A": 5_000.0}, _book(9_000.0, 5_000.0))   # -4k, isento
    ledger.accrue(FEB, {"A": 5_000.0}, _book(1_000.0, 5_000.0))   # +4k, isento
    ledger.accrue(pd.Timestamp("2026-03-31"), {}, {})
    assert ledger.carry == pytest.approx(4_000.0)


# ── Cripto: ganho de capital compensates nothing ──────────────────────────────

def test_a_crypto_loss_is_simply_lost():
    """The single most surprising rule in the table, and the one the simulator
    used to give away: one unbounded carryforward for every market meant a
    losing crypto book was scored more kindly than the Receita would."""
    ledger = tax.Ledger(tax.Rule(rate=0.15, exemption=35_000.0,
                                 carry=tax.NONE))
    ledger.accrue(JAN, {"A": 50_000.0}, _book(90_000.0, 50_000.0))  # -40k
    ledger.accrue(FEB, {}, {})
    assert ledger.carry == 0.0

    # And February's gain gets no compensação from it.
    due = ledger.accrue(pd.Timestamp("2026-03-31"), {}, {})
    ledger.accrue(FEB, {"A": 50_000.0}, _book(10_000.0, 50_000.0))
    assert ledger.close() == pytest.approx(0.15 * 40_000.0)
    assert due == 0.0


def test_a_write_off_still_nets_within_the_period_for_crypto():
    """Within one month the apuração nets every sale, carry policy or not. Only
    the carry *across* months is denied — and a write-off is a loss realised on
    a date, not a standing credit."""
    ledger = tax.Ledger(tax.Rule(rate=0.15, exemption=None, carry=tax.NONE))
    book = {"DEAD": {"cost_basis": 10_000.0, "current_value": 0.0},
            "A": {"cost_basis": 10_000.0, "current_value": 40_000.0}}
    ledger.realise_write_offs(book, JAN)
    assert "DEAD" not in book              # the ghost position is gone
    ledger.accrue(JAN, {"A": 40_000.0}, book)
    assert ledger.close() == pytest.approx(0.15 * (30_000.0 - 10_000.0))


# ── A write-off belongs to the period of its date ─────────────────────────────

def _dead(cost: float) -> dict:
    return {"DEAD": {"cost_basis": cost, "current_value": 0.0}}


def test_a_write_off_on_the_first_row_of_a_month_is_that_months_loss():
    """The rebalance books write-offs before `accrue`, and `accrue` was where the
    period rolled — so a name found dead on February's first row netted against
    January's gain, and January's tax was never charged."""
    ledger = _acoes()
    ledger.accrue(JAN, {"A": 30_000.0}, _book(20_000.0, 30_000.0))     # +10k, taxable
    ledger.realise_write_offs(_dead(10_000.0), FEB)
    assert ledger.accrue(FEB, {}, {}) == pytest.approx(0.15 * 10_000.0)
    ledger.close()
    assert ledger.carry == pytest.approx(10_000.0)


def test_a_write_off_that_rolls_the_period_leaves_the_bill_for_the_next_payment():
    """A hold row realises its write-offs too, and rolling the period there
    settles the one it closes. Nothing is paid on a hold row: the bill waits for
    the next rebalance, or for `close` when none comes."""
    ledger = _acoes()
    ledger.accrue(JAN, {"A": 30_000.0}, _book(20_000.0, 30_000.0))
    ledger.realise_write_offs(_dead(10_000.0), FEB)
    assert ledger.carry == 0.0                        # January is settled...
    assert ledger.close() == pytest.approx(0.15 * 10_000.0)    # ...and still owed
    assert ledger.carry == pytest.approx(10_000.0)


def test_nothing_to_write_off_leaves_the_period_alone():
    ledger = _acoes()
    ledger.accrue(JAN, {"A": 30_000.0}, _book(20_000.0, 30_000.0))
    ledger.realise_write_offs(_book(1.0, 1.0), FEB)
    assert ledger.accrue(JAN, {}, {}) == 0.0          # January is still open


@pytest.mark.parametrize("death, tax_2023", [
    ("2023-12-29", 0.0),                    # dies in 2023: nets the 2023 gain
    ("2024-01-31", 0.15 * 12_500.0),        # dies in 2024: cannot reach back
])
def test_a_position_that_dies_on_a_hold_row_is_lost_in_the_year_it_died(death, tax_2023):
    """End to end, exterior (the year nets and nothing crosses it). The gain is
    taken in November 2023; B is then wiped out on a hold row. Its loss belongs
    to the year it died in. It used to wait for the next live row and land in
    whatever period was still open there — 2023 either way, which erased the tax
    on a January death; and rolling at the live row alone would have pushed a
    December death into 2024."""
    from backtests.core.simulation import run_simulation

    idx = pd.DatetimeIndex(["2023-10-31", "2023-11-30", "2023-12-29",
                            "2024-01-31", "2024-02-29"])
    ret = pd.DataFrame(0.0, index=idx, columns=["A", "B"])
    ret.loc["2023-11-30", "A"] = 1.0
    ret.loc[death, "B"] = -1.0
    w = pd.DataFrame({"A": [0.5, 0.5, 0.0, 0.0, 1.0],
                      "B": [0.5, 0.5, 0.0, 0.0, 0.0]}, index=idx)
    out = run_simulation(ret, w, initial_capital=100_000, tax_rate=0.15,
                         monthly_sales_exemption=None, tax_period=tax.ANNUAL,
                         loss_carry=tax.PERIOD)
    # November sells 25k of A that cost 12.5k; B's write-off is 75k of basis.
    # 2023's bill is paid on the first rebalance after the year closed.
    assert out["tax_paid"].sum() == pytest.approx(tax_2023)
    assert out["tax_paid"].loc["2024-02-29"] == pytest.approx(tax_2023)


# ── Exterior: the year nets and settles once ──────────────────────────────────

def test_a_march_gain_nets_a_november_loss_before_anything_settles():
    """Lei 14.754/2023 compensates within the year and liquidates once, in the
    ajuste. The simulator settled at every rebalance, so a run that paid in
    March and recovered in November was charged earlier than the holder is."""
    ledger = tax.Ledger(tax.Rule(rate=0.15, exemption=None,
                                 period=tax.ANNUAL, carry=tax.PERIOD))
    assert ledger.accrue(pd.Timestamp("2026-03-31"), {"A": 60_000.0},
                         _book(10_000.0, 60_000.0)) == 0.0        # +50k
    assert ledger.accrue(NOV, {"B": 10_000.0},
                         {"B": {"cost_basis": 60_000.0,
                                "current_value": 10_000.0}}) == 0.0  # -50k
    assert ledger.close() == 0.0


def test_nothing_crosses_a_year_boundary_for_the_exterior():
    """`annual_apuracao` is explicit that a net loss for the year produces no
    tax and carries nothing forward."""
    ledger = tax.Ledger(tax.Rule(rate=0.15, exemption=None,
                                 period=tax.ANNUAL, carry=tax.PERIOD))
    ledger.accrue(NOV, {"A": 10_000.0},
                  _book(60_000.0, 10_000.0))                      # -50k in 2026
    ledger.accrue(pd.Timestamp("2027-03-31"), {"A": 60_000.0},
                  _book(10_000.0, 60_000.0))                      # +50k in 2027
    assert ledger.carry == 0.0
    assert ledger.close() == pytest.approx(0.15 * 50_000.0)


def test_the_exterior_settles_once_not_at_every_rebalance():
    ledger = tax.Ledger(tax.Rule(rate=0.15, exemption=None,
                                 period=tax.ANNUAL, carry=tax.PERIOD))
    for month in range(1, 13):
        assert ledger.accrue(pd.Timestamp(f"2026-{month:02d}-28"), {"A": 2_000.0},
                             _book(1_000.0, 2_000.0)) == 0.0
    assert ledger.close() == pytest.approx(0.15 * 12 * 1_000.0)


# ── None is not zero ──────────────────────────────────────────────────────────

def test_a_class_with_no_isencao_is_not_a_class_with_a_zero_one():
    """A period with no sales at all: `gross <= 0` is true, so an exemption of
    0.0 would make it isento and route the loss down the isento branch. `None`
    says the class has no isenção and the ordinary branch applies."""
    none_rule = tax.Ledger(tax.Rule(rate=0.15, exemption=None))
    zero_rule = tax.Ledger(tax.Rule(rate=0.15, exemption=0.0))
    for ledger in (none_rule, zero_rule):
        ledger.accrue(JAN, {"A": 0.0}, _book(10_000.0, 0.0))
    none_rule.realise_write_offs({"D": {"cost_basis": 1_000.0, "current_value": 0.0}}, JAN)
    zero_rule.realise_write_offs({"D": {"cost_basis": 1_000.0, "current_value": 0.0}}, JAN)
    none_rule.close()
    zero_rule.close()
    assert none_rule.carry == zero_rule.carry == pytest.approx(1_000.0)


# ── The registry says what the law says ───────────────────────────────────────

@pytest.mark.parametrize("market,period,carry", [
    ("B3", tax.MONTHLY, tax.FORWARD),
    ("CRYPTO", tax.MONTHLY, tax.NONE),
    ("US", tax.ANNUAL, tax.PERIOD),
])
def test_each_market_declares_the_period_and_carry_its_class_has(market, period,
                                                                 carry):
    spec = markets.get(market)
    assert (spec.tax_period, spec.loss_carry) == (period, carry)


def test_the_rate_stays_a_what_if_knob_and_the_rule_does_not():
    """Asking "what would this look like at 20%" is legitimate. Asking for a
    different isenção, apuração period or carry policy is asking for a different
    country."""
    resolved = markets.resolve_tax("CRYPTO", {
        "tax_rate": 0.20, "monthly_sales_exemption": 0.0,
        "tax_period": "anual", "loss_carry": "forward", "asset_classes": "B3"})
    assert resolved == {"tax_rate": 0.20, "monthly_sales_exemption": 35_000.0,
                        "tax_period": tax.MONTHLY, "loss_carry": tax.NONE,
                        "asset_classes": None}
    # B3 apurates per class; that is the market's, not a parameter either.
    assert markets.resolve_tax("B3", {"asset_classes": None})["asset_classes"] == "B3"
