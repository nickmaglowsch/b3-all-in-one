"""Tests for the QDL strategy (backtests/strategies/qdl.py) on synthetic data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.strategies import qdl
from backtests.strategies.qdl import QDLStrategy

N = 12
GOOD = ["AAAA3", "BBBB4", "CCCC3", "DDDD3", "EEEE4", "FFFF3"]
BAD = {"LOSS3": "net income < 0", "NEGE3": "equity < 0",
       "NANN3": "net income NaN", "MISS3": "no fundamentals column"}
TICKERS = GOOD + list(BAD)


def _shared():
    idx = pd.date_range("2018-01-31", periods=N, freq="ME")
    days = pd.bdate_range("2017-06-01", idx[-1])
    rng = np.random.default_rng(3)
    close = pd.DataFrame(np.exp(rng.normal(0, 0.01, (len(days), len(TICKERS))).cumsum(0)),
                         index=days, columns=TICKERS)
    # fundamentals keyed by the best-ADTV class, which may differ from the
    # universe ticker (BBBB3 column serves BBBB4) — the gate matches by root
    fcols = ["AAAA3", "BBBB3", "CCCC3", "DDDD3", "EEEE4", "FFFF3", "LOSS3", "NEGE3", "NANN3"]
    ni = pd.DataFrame(1e5, index=idx, columns=fcols)
    ni["LOSS3"], ni["NANN3"] = -1e5, np.nan
    eq = pd.DataFrame(1e6, index=idx, columns=fcols)
    eq["NEGE3"] = -1e6
    ret = close.resample("ME").last().pct_change().reindex(idx).fillna(0.0)
    return {
        "ret": ret,
        "adtv": pd.DataFrame(1e7, index=idx, columns=TICKERS),
        "raw_close": pd.DataFrame(10.0, index=idx, columns=TICKERS),
        "split_adj_close": close,
        "has_glitch": pd.DataFrame(0.0, index=idx, columns=TICKERS),
        "cdi_monthly": pd.Series(0.01, index=idx),
        "ibov_ret": pd.Series(0.005, index=idx),
        "f_net_income_ttm_m": ni,
        "f_equity_m": eq,
    }


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(qdl, "load_dividends", lambda _path: {})


def test_quality_gate_keeps_only_positive_income_and_equity():
    _, tw = QDLStrategy().generate_signals(
        _shared(), {"min_div_years": 0, "ewma_n": 60, "weight_cap": 0.5})
    held = tw.drop(columns=["CDI_ASSET"])
    for t, why in BAD.items():
        assert (held[t] == 0).all(), f"{t} ({why}) must never be held"
    assert (held[GOOD].iloc[2:] > 0).all().all(), "every quality name is held"
    assert np.allclose(tw.iloc[2:].sum(axis=1), 1.0)


def test_too_few_quality_names_parks_in_cdi():
    shared = _shared()
    shared["f_equity_m"] = shared["f_equity_m"] * -1   # nobody passes the gate
    _, tw = QDLStrategy().generate_signals(shared, {"min_div_years": 0, "ewma_n": 60})
    assert (tw["CDI_ASSET"].iloc[2:] == 1.0).all()
    assert (tw.drop(columns=["CDI_ASSET"]) == 0).all().all()
