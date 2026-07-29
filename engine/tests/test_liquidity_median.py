"""The liquidity screen reads fin_vol in the units the DB stores it in.

`infer_volume_scale` used to sniff those units and multiply by 100 when the
universe's busiest name looked too quiet — a guard against a legacy convention
where core/data.py halved VOLTOT's implied decimals a second time. That
convention is gone (core/data.py:290: the parser divides on write, the column is
BRL), so the sniffer could only ever misfire: any genuinely illiquid universe —
five small caps, a sector sleeve — trips the R$50M threshold and gets its ADTV
inflated 100x, which passes every name straight through `min_adtv`.
"""
from __future__ import annotations

import pandas as pd

from backtests.strategies.sp500_b3 import LIQUIDITY_WINDOW, liquidity_median

TICKERS = ["SMLL3", "TINY4"]


def _fin_vol(level: float, n: int = LIQUIDITY_WINDOW + 5) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(level, index=idx, columns=TICKERS)


def test_a_quiet_universe_keeps_its_real_volume():
    """R$1M/day is R$1M/day, not R$100M/day."""
    med = liquidity_median(_fin_vol(1e6))
    assert med.iloc[-1].tolist() == [1e6, 1e6]


def test_a_quiet_universe_is_screened_out_by_min_adtv():
    """The screen the inflation used to defeat: R$1M/day fails a R$2M floor."""
    med = liquidity_median(_fin_vol(1e6))
    assert not (med.iloc[-1] >= 2e6).any()


def test_a_liquid_universe_is_unchanged_too():
    med = liquidity_median(_fin_vol(1e9))
    assert med.iloc[-1].tolist() == [1e9, 1e9]


def test_missing_sessions_count_as_no_volume_not_as_gaps():
    """A ticker that did not trade traded R$0 — it must not be dropped from the
    median, which would flatter an intermittently-traded name."""
    fv = _fin_vol(1e6)
    fv.iloc[-40:, fv.columns.get_loc("TINY4")] = float("nan")
    med = liquidity_median(fv)
    assert med.iloc[-1]["TINY4"] == 0.0     # 40 blank sessions of 63 → median 0
    assert med.iloc[-1]["SMLL3"] == 1e6     # the name that did trade is untouched


def test_window_is_a_parameter_because_two_leg_value_uses_42():
    med = liquidity_median(_fin_vol(1e6), window=42)
    assert med.iloc[41].notna().all() and med.iloc[40].isna().all()
