"""Self-check for portfolio_index: TWR, ticker norm, splits, RF sleeve."""
import pandas as pd

from scripts.portfolio_index import (
    norm_ticker, rf_sleeve, sleeve_of, split_factor, twr_index)

GRID = pd.bdate_range("2024-01-01", periods=4)


def test_twr_ignores_flow_size():
    # d1: buy 100, flat. d2: +10%. d3: buy 110 more, +5%. d4: sell+income, +5%.
    nav = pd.Series([100.0, 110.0, 231.0, 173.25], index=GRID)
    buys = pd.Series([100.0, 0.0, 110.0, 0.0], index=GRID)
    sells = pd.Series([0.0, 0.0, 0.0, 57.75], index=GRID)
    income = pd.Series([0.0, 0.0, 0.0, 11.55], index=GRID)
    idx = twr_index(nav, buys, sells, income)
    assert abs(idx.iloc[-1] - 100 * 1.1 * 1.05 * 1.05) < 1e-9


def test_twr_flat_with_no_capital():
    zero = pd.Series(0.0, index=GRID)
    assert (twr_index(zero, zero, zero, zero) == 100.0).all()


def test_norm_ticker():
    assert norm_ticker("PETR4F") == "PETR4"
    assert norm_ticker("B3SA3F") == "B3SA3"
    assert norm_ticker("SAPR11") == "SAPR11"
    assert norm_ticker("BRL=X") == "BRL=X"


def test_orphan_sell_backfill():
    # cumsum path 5, -2, 3: the day-2 sell of 7 includes 2 unrecorded shares,
    # so they count as held from the start; never a phantom short.
    cum = pd.DataFrame({"X": [5.0, -2.0, 3.0]}, index=GRID[:3])
    adj = cum - cum.iloc[::-1].cummin().iloc[::-1].clip(upper=0.0)
    assert list(adj["X"]) == [7.0, 0.0, 3.0]


def test_split_factor_applies_before_split_only():
    splits = pd.Series([10.0], index=[GRID[2]])
    f, rejected = split_factor(splits, GRID)
    assert list(f) == [10.0, 10.0, 1.0, 1.0]
    assert rejected == []


def test_split_factor_validates_against_trades():
    splits = pd.Series([10.0], index=[GRID[2]])
    closes = pd.Series([9.3, 9.2, 9.3, 9.2], index=GRID)
    # Real 10:1 split: Yahoo adjusted old closes down, so the old trade at
    # R$93 prices at 10x the adjusted close -> split kept.
    real_trade = pd.Series([93.0], index=[GRID[1]])
    f, rej = split_factor(splits, GRID, closes, real_trade)
    assert list(f) == [10.0, 10.0, 1.0, 1.0] and rej == []
    # Bogus entry (VINO11-style): trade prices match the closes as-is,
    # Yahoo never applied the split -> rejected.
    bogus_trade = pd.Series([9.25], index=[GRID[1]])
    f, rej = split_factor(splits, GRID, closes, bogus_trade)
    assert list(f) == [1.0, 1.0, 1.0, 1.0] and len(rej) == 1
    # ...unless a B3 qty_event within 7 days confirms the action.
    f, rej = split_factor(splits, GRID, closes, bogus_trade,
                          confirm_dates=(GRID[3],))
    assert list(f) == [10.0, 10.0, 1.0, 1.0] and rej == []


def test_sleeve_of():
    assert sleeve_of("XPLG11") == "fii" and sleeve_of("GCRA11") == "fii"
    assert sleeve_of("PETR4") == "stock" and sleeve_of("SAPR11") == "stock"
    for etf_or_bdr in ("IVVB11", "LFTB11", "XPBR31", "GPIV33"):
        assert sleeve_of(etf_or_bdr) is None


def test_etf_simulate():
    from scripts.etf_counterfactual import simulate
    grid = GRID[:3]
    unit = pd.Series([1.0, 1.0, 2.0], index=grid)  # doubles on day 3
    flows = pd.Series([100.0, -50.0, 0.0], index=grid)
    v = simulate(flows, unit)
    assert list(v) == [100.0, 50.0, 100.0]
    assert simulate(pd.Series(0.0, index=grid), unit, 10.0).iloc[-1] == 20.0


def test_rf_sleeve_accrual_and_trueup():
    grid = pd.bdate_range("2024-01-01", periods=4)
    cdi = pd.Series([1.0, 1.01, 1.0201, 1.030301], index=grid)  # 1%/day
    rf = pd.DataFrame([
        # CDB: aplic 100 on d0, redeems 104 on d2 (beat the CDI estimate).
        {"code": "CDB - A", "cat": "aplic", "Data": grid[0], "Valor": 100.0},
        {"code": "CDB - A", "cat": "resgate", "Data": grid[2], "Valor": 104.0},
        # LCI still open: aplic 200 on d1 -> accrues at CDI through d3.
        {"code": "LCI - B", "cat": "aplic", "Data": grid[1], "Valor": 200.0},
        # Orphan resgate (aplic predates reports) -> backfilled from start.
        {"code": "CDB - C", "cat": "resgate", "Data": grid[1], "Valor": 50.0},
    ])
    value, buys, sells, income, diag = rf_sleeve(rf, grid, cdi)
    assert abs(value.iloc[0] - (100 + 50 / 1.01)) < 1e-6  # CDB-A + backfilled C
    assert abs(value.iloc[1] - (101 + 200)) < 1e-6        # C redeemed, B enters
    assert abs(value.iloc[3] - 200 * 1.030301 / 1.01) < 1e-6  # only B, accrued
    assert sells[grid[2]] == 104.0 and buys[grid[0]] == 100.0
    assert diag["closed"] == 2 and abs(diag["open"] - value.iloc[3]) < 1e-6


if __name__ == "__main__":
    test_twr_ignores_flow_size()
    test_twr_flat_with_no_capital()
    test_norm_ticker()
    test_orphan_sell_backfill()
    test_split_factor_applies_before_split_only()
    test_split_factor_validates_against_trades()
    test_sleeve_of()
    test_etf_simulate()
    test_rf_sleeve_accrual_and_trueup()
    print("ok")
