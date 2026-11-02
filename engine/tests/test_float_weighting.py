"""free_float.float_adjusted: float-adjusted caps, the min-float screen, the
unknown-value fallback and as-of reads, plus the strategy wiring (SP500-style)."""
from __future__ import annotations

import pandas as pd
import pytest

import backtests.strategies.sp500_b3 as sp
from backtests.core.free_float import float_adjusted
from backtests.strategies import teva_iafd as iafd
from tests.test_sp500_b3 import make_actions, make_fund


def _ff(rows):
    """rows: (root, ref_date, version, receipt_date, pct)."""
    df = pd.DataFrame(rows, columns=["root", "ref_date", "version", "receipt_date",
                                     "free_float_pct"])
    for c in ("ref_date", "receipt_date"):
        df[c] = pd.to_datetime(df[c])
    return df


FF = _ff([
    ("AAAA", "2019-12-31", 1, "2020-05-20", 50.0),
    ("AAAA", "2020-12-31", 1, "2021-05-20", 80.0),   # received after the 2021-03 read
    ("BPAC", "2019-12-31", 1, "2020-05-20", 30.0),   # unit: company-level value
    ("TINY", "2019-12-31", 1, "2020-05-20", 9.9),
    ("EDGE", "2019-12-31", 1, "2020-05-20", 10.0),
])
SEL = pd.DataFrame({"root": ["AAAA", "BPAC", "TINY", "EDGE", "NOFF"],
                    "ticker": ["AAAA3", "BPAC11", "TINY3", "EDGE3", "NOFF3"],
                    "market_cap": [100.0, 100.0, 100.0, 100.0, 100.0]})


def test_float_cap_screen_and_unknown_fallback():
    out = float_adjusted(SEL, FF, "2021-03-31", min_pct=10.0).set_index("root")
    assert "TINY" not in out.index                       # known 9.9% < 10%
    assert out["float_cap"].to_dict() == pytest.approx(
        {"AAAA": 50.0, "BPAC": 30.0, "EDGE": 10.0, "NOFF": 100.0})  # NOFF: full cap
    assert pd.isna(out.loc["NOFF", "free_float_pct"])
    assert out["market_cap"].eq(100.0).all()             # full cap kept for screens


def test_asof_never_reads_a_filing_received_later():
    assert float_adjusted(SEL, FF, "2021-03-31").set_index("root").loc["AAAA", "float_cap"] == 50.0
    assert float_adjusted(SEL, FF, "2021-06-30").set_index("root").loc["AAAA", "float_cap"] == 80.0
    before = float_adjusted(SEL, FF, "2020-03-31")       # nothing received yet
    assert before["float_cap"].tolist() == SEL["market_cap"].tolist()


def test_no_free_float_data_is_full_cap_for_everyone():
    for ff in (None, FF.head(0)):
        out = float_adjusted(SEL, ff, "2021-03-31", min_pct=15.0)
        assert out["float_cap"].tolist() == SEL["market_cap"].tolist()


def test_iafd_weights_on_float_cap():
    sel = float_adjusted(SEL, FF, "2021-03-31", 15.0)
    w = iafd.iafd_weights(sel, pd.Index(["AAAA", "BPAC"]), set(), pd.Index(["AAAA", "BPAC"]),
                          "float_cap")
    assert w.to_dict() == pytest.approx({"AAAA3": 0.625, "BPAC11": 0.375})


@pytest.mark.parametrize("adj, expected", [("Yes", {"AAAA3": 0.625, "BPAC3": 0.375}),
                                           ("No", {t: 1 / 3 for t in ("AAAA3", "BPAC3",
                                                                      "TINY3")})])
def test_sp500_strategy_weights_by_float_cap(monkeypatch, adj, expected):
    days = pd.date_range("2020-12-01", "2021-03-31", freq="B")
    tickers = ["AAAA3", "BPAC3", "TINY3"]
    close = pd.DataFrame(10.0, index=days, columns=tickers)
    fin_vol = pd.DataFrame(1e8, index=days, columns=tickers)
    ret = pd.DataFrame(0.0, index=[pd.Timestamp("2021-03-31")], columns=tickers)
    fund = make_fund([(r, "2020-12-31", "2021-02-01", 1, 1e5, 1e6, 1e9)
                      for r in ("AAAA", "BPAC", "TINY")])
    monkeypatch.setattr(sp, "load_fundamentals_pit_raw", lambda db: fund)
    monkeypatch.setattr(sp, "load_stock_actions", lambda db: make_actions())
    monkeypatch.setattr(sp, "liquidity_median", lambda fv: fv.rolling(21).median())
    shared = {"ret": ret, "close_px": close, "fin_vol": fin_vol, "free_float_pit": FF}
    _, tw = sp.SP500B3Strategy().generate_signals(
        shared, {"min_constituents": 1, "db_path": "unused", "float_adjusted": adj})
    row = tw.iloc[0]
    # Yes: TINY (9.9%) fails S&P's 10% float screen; No: full caps, no screen
    assert row[row > 0].to_dict() == pytest.approx(expected)
