"""Tests for the Top-N-by-market-cap strategy (backtests/strategies/top_mcap.py).

Synthetic in-memory frames, no database. Covers the require_earnings=False
flag on select_constituents, the top-N selection + weighting logic, and the
strategy's own rebalance loop across every COMMON_REBALANCE_FREQ choice.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtests.strategies import top_mcap
from backtests.strategies.sp500_b3 import compute_weights, select_constituents
from tests.test_sp500_b3 import make_actions, make_fund

T = pd.Timestamp("2020-09-30")


def _universe():
    # BIGG: R$30bn cap but NEGATIVE net income; MIDD: R$20bn; SMLL: R$10bn.
    fund = make_fund([
        ("BIGG", "2020-06-30", "2020-08-01", 1, -50_000.0, 1_000_000.0, 3e9),
        ("MIDD", "2020-06-30", "2020-08-01", 1, 100_000.0, 1_000_000.0, 2e9),
        ("SMLL", "2020-06-30", "2020-08-01", 1, 100_000.0, 1_000_000.0, 1e9),
    ])
    close = pd.Series({"BIGG3": 10.0, "MIDD3": 10.0, "SMLL3": 10.0})
    vol = pd.Series({"BIGG3": 5e6, "MIDD3": 5e6, "SMLL3": 5e6})
    return fund, close, vol


def test_require_earnings_false_admits_negative_ni():
    fund, close, vol = _universe()
    sel = select_constituents(
        T, fund, close, vol, make_actions(),
        min_market_cap=0.0, min_adtv=0.0, require_earnings=False,
    )
    assert set(sel["root"]) == {"BIGG", "MIDD", "SMLL"}
    # default (require_earnings=True) still excludes the loss-maker
    sel_strict = select_constituents(
        T, fund, close, vol, make_actions(), min_market_cap=0.0, min_adtv=0.0,
    )
    assert set(sel_strict["root"]) == {"MIDD", "SMLL"}


def test_corrupted_share_count_excluded():
    # Real-world failure: TELB/TOYB/COCE filings carry ~1e11-1e12 shares
    # (fake trillion-BRL companies) while the stock trades ~R$1M/day. The
    # turnover sanity guard must drop them even though they rank #1 by cap.
    fund = make_fund([
        ("FAKE", "2020-06-30", "2020-08-01", 1, 100_000.0, 1_000_000.0, 1e12),
        ("REAL", "2020-06-30", "2020-08-01", 1, 100_000.0, 1_000_000.0, 2e9),
    ])
    close = pd.Series({"FAKE4": 0.6, "REAL3": 30.0})   # FAKE "cap" = R$600bn
    vol = pd.Series({"FAKE4": 1e6, "REAL3": 3e8})
    sel = select_constituents(
        T, fund, close, vol, make_actions(),
        min_market_cap=0.0, min_adtv=0.0, require_earnings=False,
    )
    assert set(sel["root"]) == {"REAL"}


def test_per_class_market_cap_avoids_unit_inflation():
    # BPAC-style: most liquid listing is the unit (1 ON + 2 PN). Old formula
    # (total shares x unit price) tripled the cap; per-class pricing must use
    # BTGU3 (ON) and BTGU5 (PN) closes instead of the BTGU11 unit close.
    fund = make_fund([("BTGU", "2020-06-30", "2020-08-01", 1, 100_000.0, 1_000_000.0, 3e9)])
    fund["shares_on"] = 1e9
    fund["shares_pn"] = 2e9
    close = pd.Series({"BTGU11": 30.0, "BTGU3": 10.0, "BTGU5": 10.0})
    vol = pd.Series({"BTGU11": 5e8, "BTGU3": 1e6, "BTGU5": 1e6})
    sel = select_constituents(
        T, fund, close, vol, make_actions(),
        min_market_cap=0.0, min_adtv=0.0, require_earnings=False,
    )
    assert len(sel) == 1
    assert sel["ticker"].iloc[0] == "BTGU11"        # unit still the traded instrument
    assert sel["market_cap"].iloc[0] == pytest.approx(3e10)   # not 9e10


def test_top_n_and_weightings():
    fund, close, vol = _universe()
    sel = select_constituents(
        T, fund, close, vol, make_actions(),
        min_market_cap=0.0, min_adtv=0.0, require_earnings=False,
    )
    top2 = sel.nlargest(2, "market_cap")
    assert set(top2["root"]) == {"BIGG", "MIDD"}

    caps = top2.set_index("ticker")["market_cap"]
    w_cap = compute_weights(caps, "market_cap")
    assert abs(w_cap.sum() - 1.0) < 1e-12
    assert abs(w_cap["BIGG3"] - 3.0 / 5.0) < 1e-12

    w_eq = compute_weights(caps, "equal")
    assert (w_eq == 0.5).all()


# ── End-to-end: generate_signals over a real rebalance grid ───────────────────
# The tests above exercise the shared selection functions; these drive the
# strategy itself, which is where the rebalance calendar lives. TopMarketCap
# takes its grid from shared["ret"].index (built by build_shared_data at the
# run's freq), so "does it honour rebalance_freq" is only answerable here.

FREQS = ["ME", "QE", "W-FRI"]   # the COMMON_REBALANCE_FREQ choices


def _daily_frames(tickers):
    """Daily close + financial volume, flat. History starts six months before
    the 2020 grid so the 63-trading-day liquidity median is warm on the first
    rebalance (a cold median is NaN -> 0 -> filtered, and every early row would
    be empty for reasons that have nothing to do with the frequency)."""
    daily = pd.date_range("2019-07-01", "2020-12-31", freq="B")
    close = pd.DataFrame(10.0, index=daily, columns=tickers)
    fin_vol = pd.DataFrame(5e6, index=daily, columns=tickers)
    return close, fin_vol


def _four_companies():
    # caps at close 10.0: AAAA R$40bn, BBBB R$30bn, CCCC R$20bn, DDDD R$10bn.
    # Filed 2020-01-02, inside the 400-day staleness window for the whole grid.
    return make_fund([
        ("AAAA", "2019-12-31", "2020-01-02", 1, 1e5, 1e6, 4e9),
        ("BBBB", "2019-12-31", "2020-01-02", 1, 1e5, 1e6, 3e9),
        ("CCCC", "2019-12-31", "2020-01-02", 1, 1e5, 1e6, 2e9),
        ("DDDD", "2019-12-31", "2020-01-02", 1, 1e5, 1e6, 1e9),
    ])


@pytest.fixture
def signals(monkeypatch):
    """Run TopMarketCapStrategy.generate_signals on synthetic frames, with the
    two DB loaders stubbed (patched on top_mcap, which imports them by value)."""
    tickers = ["AAAA3", "BBBB3", "CCCC3", "DDDD3"]
    close, fin_vol = _daily_frames(tickers)
    monkeypatch.setattr(top_mcap, "load_fundamentals_pit_raw",
                        lambda _db: _four_companies())
    monkeypatch.setattr(top_mcap, "load_stock_actions", lambda _db: make_actions())

    def run(freq="ME", **params):
        idx = pd.date_range("2020-01-01", "2020-12-31", freq=freq)
        ret = pd.DataFrame(0.01, index=idx, columns=tickers)
        shared = {"ret": ret, "close_px": close, "fin_vol": fin_vol}
        return top_mcap.TopMarketCapStrategy().generate_signals(
            shared, {"top_n": 2, "weighting": "market_cap",
                     "min_adtv": 0.0, **params})

    return run


@pytest.mark.parametrize("freq", FREQS)
def test_rebalances_on_every_frequency(signals, freq):
    # One fully-weighted row per date on the grid — weekly included, which is
    # the case a quarterly-only selection loop would silently drop.
    _ret, tw = signals(freq)
    assert list(tw.index) == list(pd.date_range("2020-01-01", "2020-12-31", freq=freq))
    assert (tw.sum(axis=1) > 0).all(), "every rebalance date must be invested"
    assert tw.sum(axis=1).max() == pytest.approx(1.0)


@pytest.mark.parametrize("freq", FREQS)
def test_holds_the_largest_by_cap_at_every_frequency(signals, freq):
    _ret, tw = signals(freq)
    held = sorted(tw.columns[(tw > 0).any()])
    assert held == ["AAAA3", "BBBB3"], "top_n=2 must be the two largest caps"


def test_top_n_controls_basket_size(signals):
    _ret, tw = signals("ME", top_n=3)
    assert sorted(tw.columns[(tw > 0).any()]) == ["AAAA3", "BBBB3", "CCCC3"]
    assert (tw > 0).sum(axis=1).eq(3).all()


def test_weighting_schemes(signals):
    _ret, cap = signals("ME", top_n=2, weighting="market_cap")
    row = cap.iloc[-1]
    # 40bn / 30bn -> 4/7 and 3/7
    assert row["AAAA3"] == pytest.approx(4 / 7)
    assert row["BBBB3"] == pytest.approx(3 / 7)

    _ret, eq = signals("ME", top_n=2, weighting="equal")
    assert eq.iloc[-1]["AAAA3"] == pytest.approx(0.5)
    assert eq.iloc[-1]["BBBB3"] == pytest.approx(0.5)


def test_min_adtv_filters_illiquid_names(signals):
    # Every name trades R$5M/day; a R$6M floor empties the universe, and an
    # empty selection must park in cash rather than raise.
    _ret, tw = signals("ME", min_adtv=6e6)
    assert (tw.sum(axis=1) == 0).all()
