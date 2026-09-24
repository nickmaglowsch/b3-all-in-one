"""Tests for the QMV strategy (backtests/strategies/qmv.py) on synthetic data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.strategies.qmv import QMVStrategy, TRADING_DAYS_YEAR

N = 30
TICKERS = ["GOOD4", "NICE3", "FINE4", "OKOK3", "SOLD3", "BADD3"]


def _shared(daily_ibov_vol=0.02):
    idx = pd.date_range("2018-01-31", periods=N, freq="ME")
    rng = np.random.default_rng(7)
    ret = pd.DataFrame(rng.normal(0.01, 0.05, (N, len(TICKERS))), index=idx, columns=TICKERS)
    ret["BADD3"] += 0.05   # strong momentum on the junk name — gate must catch it
    log_ret = np.log1p(ret)
    # fundamentals: everyone profitable except BADD3
    ni = pd.DataFrame(1e5, index=idx, columns=TICKERS)
    ni["BADD3"] = -1e5
    eq = pd.DataFrame(1e6, index=idx, columns=TICKERS)
    return {
        "ret": ret,
        "log_ret": log_ret,
        "adtv": pd.DataFrame(1e7, index=idx, columns=TICKERS),
        "raw_close": pd.DataFrame(10.0, index=idx, columns=TICKERS),
        "has_glitch": pd.DataFrame(0.0, index=idx, columns=TICKERS),
        "cdi_monthly": pd.Series(0.01, index=idx),
        "ibov_ret": pd.Series(0.005, index=idx),
        "ibov_vol_monthly": pd.Series(daily_ibov_vol, index=idx),
        "f_net_income_ttm_m": ni,
        "f_equity_m": eq,
    }


def test_quality_gate_excludes_unprofitable():
    r, tw = QMVStrategy().generate_signals(_shared(), {"top_n": 5})
    assert (tw["BADD3"] == 0).all(), "unprofitable ticker must never be held"
    assert tw.drop(columns=["CDI_ASSET"]).to_numpy().sum() > 0, "equities must be held"


def test_vol_target_scales_equity_and_cdi_gets_rest():
    daily_vol = 0.02  # ann ≈ 31.7% > 15% target -> scaled down
    r, tw = QMVStrategy().generate_signals(_shared(daily_vol), {"top_n": 5})
    expected_frac = 0.15 / (daily_vol * np.sqrt(TRADING_DAYS_YEAR))
    active = tw[tw.drop(columns=["CDI_ASSET"]).sum(axis=1) > 0]
    assert len(active) > 0
    row = active.iloc[-1]
    assert row.drop("CDI_ASSET").sum() == pytest.approx(expected_frac, rel=1e-6)
    assert row["CDI_ASSET"] == pytest.approx(1 - expected_frac, rel=1e-6)
    assert row.sum() == pytest.approx(1.0)


def test_ibov_vol_aligned_by_label_not_position():
    # Yahoo's ^BVSP calendar can start later than the strategy calendar;
    # positional iloc indexing then reads the wrong month (or falls off the
    # end and silently defaults to fully invested). Alignment must be by label.
    shared = _shared()
    idx = shared["ret"].index
    shared["ibov_vol_monthly"] = pd.Series(0.02, index=idx[-10:])
    r, tw = QMVStrategy().generate_signals(shared, {"top_n": 5})
    expected_frac = 0.15 / (0.02 * np.sqrt(TRADING_DAYS_YEAR))
    active = tw[tw.drop(columns=["CDI_ASSET"]).sum(axis=1) > 0]
    row = active.iloc[-1]
    assert row.drop("CDI_ASSET").sum() == pytest.approx(expected_frac, rel=1e-6)


def test_low_vol_regime_fully_invested():
    daily_vol = 0.005  # ann ≈ 7.9% < 15% target -> fraction capped at 1.0
    r, tw = QMVStrategy().generate_signals(_shared(daily_vol), {"top_n": 5})
    active = tw[tw.drop(columns=["CDI_ASSET"]).sum(axis=1) > 0]
    row = active.iloc[-1]
    assert row.drop("CDI_ASSET").sum() == pytest.approx(1.0)
    assert row["CDI_ASSET"] == pytest.approx(0.0)


def test_quality_gate_matches_fundamentals_by_root():
    from backtests.strategies.qmv import quality_gate

    # f_* columns are one best-ADTV class per company: AAAA4 serves AAAA3, and
    # the first column per root wins
    ni = pd.DataFrame([[1.0, -1.0, 1.0, 1.0, np.nan]],
                      columns=["AAAA4", "AAAA3", "BBBB3", "CCCC3", "DDDD3"])
    eq = pd.DataFrame([[1.0, 1.0, 1.0, -1.0, 1.0]], columns=ni.columns)
    valid = pd.Series(1.0, index=["AAAA3", "BBBB4", "CCCC3", "DDDD3", "EEEE3"])

    assert list(quality_gate(valid, ni, eq, 0).index) == ["AAAA3", "BBBB4"]
    # no equity frame -> nobody has positive equity
    assert quality_gate(valid, ni, pd.DataFrame(), 0).empty
