"""The headline numbers: Return / Vol / Sharpe / Max DD / Calmar.

Every backtest the product shows is summarised through `build_metrics`, and the
summary is the whole point of a run — a Sharpe off by a square root is invisible
until someone allocates money on it. `test_daily_drawdown.py` covers the daily
reconstruction; this covers the arithmetic on top of it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.core import metrics as m


def monthly(*values) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2020-01-31", periods=len(values),
                                                 freq="ME"), dtype=float)


# --------------------------------------------------------------------------
# Annualization
# --------------------------------------------------------------------------

def test_annual_return_compounds_rather_than_averaging():
    """24 months at +1% is 12.68% a year, not 12%."""
    assert m.ann_return(monthly(*[0.01] * 24)) == pytest.approx(1.01 ** 12 - 1)


def test_a_half_year_of_returns_is_scaled_up_to_a_year():
    assert m.ann_return(monthly(*[0.01] * 6)) == pytest.approx(1.01 ** 12 - 1)


def test_an_empty_series_annualizes_to_zero_instead_of_dividing_by_zero():
    assert m.ann_return(pd.Series(dtype=float)) == 0.0


def test_volatility_is_scaled_by_the_square_root_of_the_period_count():
    ret = monthly(0.05, -0.03, 0.02, -0.01, 0.04, 0.0)
    assert m.ann_vol(ret) == pytest.approx(ret.std() * np.sqrt(12))


def test_quarterly_data_annualizes_on_four_periods_not_twelve():
    ret = monthly(0.05, -0.03, 0.02, -0.01)
    assert m.ann_vol(ret, periods_per_year=4) == pytest.approx(ret.std() * 2.0)


def test_sharpe_is_the_excess_mean_over_the_deviation_annualized():
    ret = monthly(0.05, -0.03, 0.02, -0.01, 0.04, 0.0)
    rf = 0.06
    assert m.sharpe(ret, risk_free=rf) == pytest.approx(
        (ret.mean() - rf / 12) / ret.std() * np.sqrt(12))


def test_a_riskless_line_reports_a_sharpe_of_zero_and_not_infinity():
    """Zero deviation is a division by zero; a flat CDI sleeve must not print
    inf and rank first in the gallery."""
    assert m.sharpe(monthly(*[0.01] * 12)) == 0.0


# --------------------------------------------------------------------------
# Drawdown
# --------------------------------------------------------------------------

def test_max_drawdown_measures_from_the_running_peak():
    # +20% then -50%: the trough is 0.6 against a peak of 1.2.
    assert m.max_dd(monthly(0.20, -0.50)) == pytest.approx(0.6 / 1.2 - 1)


def test_a_line_that_only_goes_up_has_no_drawdown():
    assert m.max_dd(monthly(0.01, 0.02, 0.03)) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# value_to_ret — deposits are not performance
# --------------------------------------------------------------------------

def test_a_deposit_is_not_counted_as_a_gain():
    """Without stripping contributions, a monthly buy-in reads as return and
    inflates Return, Vol and Sharpe all at once."""
    values = monthly(1_000.0, 2_000.0, 3_000.0)     # +1000 deposited each month
    contribs = monthly(0.0, 1_000.0, 1_000.0)
    assert list(m.value_to_ret(values, contribs)) == pytest.approx([0.0, 0.0, 0.0])
    # The naive path is what this exists to avoid.
    assert m.value_to_ret(values).iloc[1] == pytest.approx(1.0)


def test_a_run_that_starts_from_zero_capital_does_not_report_infinite_return():
    values = monthly(0.0, 1_000.0, 1_100.0)
    contribs = monthly(0.0, 1_000.0, 0.0)
    ret = m.value_to_ret(values, contribs)
    assert np.isfinite(ret).all()
    assert ret.iloc[2] == pytest.approx(0.10)


# --------------------------------------------------------------------------
# build_metrics — what the API returns
# --------------------------------------------------------------------------

def test_the_summary_carries_every_field_the_product_renders():
    out = m.build_metrics(monthly(0.05, -0.03, 0.02), "Pre-Tax")
    assert set(out) == {"Strategy", "Ann. Return (%)", "Ann. Volatility (%)",
                        "Sharpe", "Max Drawdown (%)", "Calmar"}
    assert out["Strategy"] == "Pre-Tax"


def test_the_daily_path_is_what_sets_max_drawdown_when_one_is_supplied():
    """The rebalance-cadence curve is blind to a crash that recovers before the
    next mark — reporting that as a 0% drawdown is the bug this guards."""
    flat = monthly(0.0, 0.0, 0.0)
    daily = pd.Series([100.0, 60.0, 100.0],
                      index=pd.date_range("2020-01-01", periods=3, freq="D"))
    assert m.build_metrics(flat, "x")["Max Drawdown (%)"] == 0.0
    assert m.build_metrics(flat, "x", daily_values=daily)["Max Drawdown (%)"] == -40.0


def test_a_daily_path_too_short_to_have_a_drawdown_falls_back_to_the_cadence():
    one_point = pd.Series([100.0], index=pd.date_range("2020-01-01", periods=1))
    out = m.build_metrics(monthly(0.20, -0.50), "x", daily_values=one_point)
    assert out["Max Drawdown (%)"] == pytest.approx(round((0.6 / 1.2 - 1) * 100, 2))


def test_calmar_is_zero_rather_than_infinite_when_nothing_ever_dropped():
    assert m.build_metrics(monthly(0.01, 0.02), "x")["Calmar"] == 0.0


def test_calmar_is_the_annual_return_over_the_depth_of_the_worst_fall():
    out = m.build_metrics(monthly(0.20, -0.50, 0.10), "x")
    assert out["Calmar"] == pytest.approx(
        round(out["Ann. Return (%)"] / abs(out["Max Drawdown (%)"]), 2), abs=0.01)


# --------------------------------------------------------------------------
# strategy_daily_values — assembling the daily path out of `shared`
# --------------------------------------------------------------------------

def daily_frame(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.date_range("2020-01-01", periods=n, freq="D"))


def weights_on(index, **cols) -> pd.DataFrame:
    return pd.DataFrame(cols, index=index)


def test_without_daily_prices_there_is_no_daily_path_to_return():
    """Callers read the empty Series as 'fall back to cadence drawdown'."""
    assert m.strategy_daily_values({}, pd.DataFrame()).empty


def test_the_cash_sleeve_is_priced_off_the_cdi_series():
    """CDI_ASSET has no B3 price column; without wiring it in, a half-cash book
    reconstructs as half its real NAV path."""
    close = daily_frame(PETR4=[100.0, 100.0, 100.0, 100.0])
    cdi = pd.Series([0.0, 0.01, 0.01, 0.01], index=close.index)
    tw = weights_on(close.index[:1], PETR4=[0.5], CDI_ASSET=[0.5])
    nav = m.strategy_daily_values(
        {"split_adj_close": close, "cdi_daily": cdi}, tw, initial_capital=100.0)
    # Equities flat, cash compounding at 1% a day over three days.
    assert nav.iloc[-1] == pytest.approx(50.0 + 50.0 * 1.01 ** 3)


def test_a_downloaded_etf_sleeve_is_taken_from_the_frame_that_carries_it():
    """FixedWeight blends stash IVVB11 & friends outside the B3 price matrix."""
    close = daily_frame(PETR4=[100.0, 100.0, 100.0])
    ivvb = pd.Series([0.0, 0.10, 0.0], index=close.index)
    tw = weights_on(close.index[:1], PETR4=[0.5], IVVB11=[0.5])
    nav = m.strategy_daily_values(
        {"split_adj_close": close, "_daily_asset_ret": {"IVVB11": ivvb}},
        tw, initial_capital=100.0)
    assert nav.iloc[-1] == pytest.approx(50.0 + 55.0)


def test_display_prints_a_row_for_every_metric_and_a_column_per_strategy(capsys):
    a = m.build_metrics(monthly(0.05, -0.03), "Pre-Tax")
    b = m.build_metrics(monthly(0.04, -0.02), "After-Tax")
    m.display_metrics_table([a, b])
    out = capsys.readouterr().out
    assert "Pre-Tax" in out and "After-Tax" in out
    for key in a:
        if key != "Strategy":
            assert key in out
