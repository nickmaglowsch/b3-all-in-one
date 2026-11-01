"""Tests for the Teva IAFD replica (backtests/strategies/teva_iafd.py).

Synthetic in-memory frames, no database: the annual-statement snapshot (and its
no-look-ahead rule), the fundamental / liquidity screens, the weighting, and
the strategy's once-a-year rebalance loop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.strategies import teva_iafd as iafd
from tests.test_sp500_b3 import make_actions, make_fund


def make_dfp(rows):
    """rows: (root, fiscal_year, filing_date, version, revenue, ni, ebitda, equity, net_debt)."""
    df = pd.DataFrame(rows, columns=["ticker", "fiscal_year", "filing_date", "filing_version",
                                     *iafd.DFP_METRICS])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df


def five_years(root, ni=150.0, last=2019, filed_month="03-01", **over):
    """Five annual DFPs ending `last`: revenue 1000, NI `ni`, EBITDA 300,
    equity 1000, net debt 600 — passes every screen at the defaults."""
    vals = dict(revenue=1000.0, ebitda=300.0, equity=1000.0, net_debt=600.0) | over
    return [(root, y, f"{y + 1}-{filed_month}", 1, vals["revenue"], ni, vals["ebitda"],
             vals["equity"], vals["net_debt"]) for y in range(last - 4, last + 1)]


# ── annual_snapshot ───────────────────────────────────────────────────────────

def test_snapshot_reads_latest_fiscal_year_known_at_t():
    dfp = make_dfp(five_years("GOOD"))
    snap = iafd.annual_snapshot(dfp, pd.Timestamp("2020-03-31"))
    assert snap.loc["GOOD", "fiscal_year"] == 2019
    assert snap.loc["GOOD", "profit_years"] == 5
    # before FY2019 was filed, FY2018 is the newest — and only 4 years exist then
    snap = iafd.annual_snapshot(dfp, pd.Timestamp("2020-02-28"))
    assert snap.loc["GOOD", "fiscal_year"] == 2018
    assert snap.loc["GOOD", "profit_years"] == 4


def test_snapshot_ignores_restatement_filed_after_t():
    rows = five_years("GOOD")
    rows.append(("GOOD", 2019, "2020-06-01", 2, 1000.0, -5.0, 300.0, 1000.0, 600.0))
    dfp = make_dfp(rows)
    assert iafd.annual_snapshot(dfp, pd.Timestamp("2020-03-31")).loc["GOOD", "net_income"] == 150.0
    later = iafd.annual_snapshot(dfp, pd.Timestamp("2020-06-30"))
    assert later.loc["GOOD", "net_income"] == -5.0 and later.loc["GOOD", "profit_years"] == 4


def test_snapshot_counts_a_loss_year_and_drops_stale():
    rows = five_years("LOSS")
    rows[1] = rows[1][:5] + (-10.0,) + rows[1][6:]          # FY2016 loss
    dfp = make_dfp(rows + five_years("OLDD", last=2017))
    snap = iafd.annual_snapshot(dfp, pd.Timestamp("2020-03-31"))
    assert snap.loc["LOSS", "profit_years"] == 4
    assert "OLDD" not in snap.index                         # newest DFP > 400 days old


def test_snapshot_follows_the_company_across_a_rename():
    rows = five_years("OLDR", last=2017) + five_years("NEWR", last=2019)[-2:]
    dfp = make_dfp(rows).assign(cnpj="C1")
    snap = iafd.annual_snapshot(dfp, pd.Timestamp("2020-03-31"))
    assert list(snap.index) == ["NEWR"] and snap.loc["NEWR", "profit_years"] == 5


def test_late_refiled_old_year_counts_for_the_sign_only():
    rows = [r for r in five_years("ITUB", last=2018) if r[1] != 2016]
    rows.append(("ITUB", 2016, "2020-03-18", 2, 1000.0, 150.0, 300.0, 1000.0, 600.0))
    snap = iafd.annual_snapshot(make_dfp(rows), pd.Timestamp("2019-03-31"))
    assert snap.loc["ITUB", "fiscal_year"] == 2018 and snap.loc["ITUB", "profit_years"] == 5
    # the newest year is never taken from the future
    early = iafd.annual_snapshot(make_dfp(rows), pd.Timestamp("2019-02-28"))
    assert early.loc["ITUB", "fiscal_year"] == 2017


def test_all_zero_filing_falls_back_to_previous_year():
    rows = five_years("TIMS") + [("TIMS", 2020, "2021-03-01", 1, 0.0, 0.0, 0.0, 0.0, None)]
    snap = iafd.annual_snapshot(make_dfp(rows), pd.Timestamp("2021-03-31"))
    assert snap.loc["TIMS", "fiscal_year"] == 2019 and "TIMS" in iafd.fundamental_pass(snap)


def test_company_first_trade_spans_every_root_of_the_cnpj():
    first = pd.Series({"AMBV": pd.Timestamp("2000-01-03"), "ABEV": pd.Timestamp("2013-11-11"),
                       "SOLO": pd.Timestamp("2019-05-02")})
    companies = pd.DataFrame({"root": ["AMBV", "ABEV"], "cnpj": ["C1", "C1"]})
    got = iafd.company_first_trade(first, companies)
    assert got["ABEV"] == pd.Timestamp("2000-01-03") and got["SOLO"] == pd.Timestamp("2019-05-02")


# ── fundamental_pass ──────────────────────────────────────────────────────────

def _snap(**cols):
    base = dict(revenue=1000.0, net_income=150.0, ebitda=300.0, equity=1000.0,
                net_debt=600.0, profit_years=5)
    return pd.DataFrame([base | cols], index=["X"])


@pytest.mark.parametrize("cols,ok", [
    ({}, True),
    ({"profit_years": 4}, False),                       # not 5 straight profitable years
    ({"net_income": 90.0, "revenue": 500.0}, False),    # ROE 9% <= 10%
    ({"revenue": 2000.0}, False),                       # margin 7.5% <= 8%
    ({"revenue": 0.0}, True),                           # no revenue line: margin skipped
    ({"net_debt": 900.0}, False),                       # 3.0x EBITDA, not < 3
    ({"net_debt": np.nan}, True),                       # banks: no net debt
    ({"net_debt": -50.0, "ebitda": -1.0}, True),        # net cash passes
    ({"net_debt": 10.0, "ebitda": -1.0}, False),        # debt with negative EBITDA
    ({"equity": -1000.0, "net_income": -150.0}, False),  # negative/negative is not ROE
])
def test_fundamental_screens(cols, ok):
    assert ("X" in iafd.fundamental_pass(_snap(**cols))) is ok


def test_financials_are_exempt_from_leverage():
    s = _snap(net_debt=5000.0)
    assert "X" not in iafd.fundamental_pass(s)
    assert "X" in iafd.fundamental_pass(s, financial_roots=["X"])


# ── liquidity_pass ────────────────────────────────────────────────────────────

def test_liquidity_needs_every_session_and_two_months_of_volume():
    days = pd.bdate_range("2020-01-01", "2020-03-31")
    fv = pd.DataFrame({"AAAA3": 6e6, "GAPP3": 6e6, "THIN3": 6e6, "FEBB3": 6e6}, index=days)
    fv.loc["2020-03-10", "GAPP3"] = np.nan                  # one silent session in March
    fv["THIN3"] = 2e6                                       # ~R$44mm/month
    fv.loc[fv.index.month == 2, "FEBB3"] = 1e6              # thin in February only
    assert list(iafd.liquidity_pass(fv, pd.Timestamp("2020-03-31"))) == ["AAAA3"]


# ── weights ───────────────────────────────────────────────────────────────────

def test_weights_are_market_cap_over_survivors():
    sel = pd.DataFrame({"root": ["AAAA", "BBBB", "CCCC", "DDDD"],
                        "ticker": ["AAAA3", "BBBB4", "CCCC3", "DDDD3"],
                        "market_cap": [30e9, 10e9, 50e9, 50e9]})
    w = iafd.iafd_weights(sel, pd.Index(["AAAA", "BBBB", "CCCC", "DDDD"]),
                          excluded={"CCCC"}, listed=pd.Index(["AAAA", "BBBB", "CCCC"]))
    assert w.to_dict() == pytest.approx({"AAAA3": 0.75, "BBBB4": 0.25})


# ── strategy loop ─────────────────────────────────────────────────────────────

SHARED: dict = {}
ROOTS = ["GOOD", "BIGG", "RETL", "LOSS", "LATE", "NEWW"]


@pytest.fixture
def signals(monkeypatch):
    # 1e9 shares x R$10 = R$10bn each (BIGG R$30bn)
    fund = make_fund([(r, f"{y - 1}-12-31", f"{y}-02-15", 1, 100.0, 1000.0,
                       3e9 if r == "BIGG" else 1e9) for r in ROOTS for y in (2020, 2021, 2022)])
    rows = []
    for r in ["GOOD", "BIGG", "RETL", "NEWW"]:
        rows += five_years(r) + five_years(r, last=2020)
    rows += five_years("LOSS", ni=-1.0)
    # LATE fails ROE on FY2019 (filed in time), passes on FY2020 — and a FY2019
    # restatement that would pass lands in April, after the March rebalance.
    rows += five_years("LATE", ni=50.0, revenue=500.0)
    rows.append(("LATE", 2019, "2020-04-15", 2, 1000.0, 150.0, 300.0, 1000.0, 600.0))
    rows.append(("LATE", 2020, "2021-03-01", 1, 1000.0, 150.0, 300.0, 1000.0, 600.0))
    dfp = make_dfp(rows)
    first = pd.Series({r: pd.Timestamp("2010-01-04") for r in ROOTS})
    first["NEWW"] = pd.Timestamp("2016-01-04")              # 5y old only from 2021
    monkeypatch.setattr(iafd, "load_fundamentals_pit_raw", lambda _db: fund)
    monkeypatch.setattr(iafd, "load_dfp", lambda _db: dfp)
    monkeypatch.setattr(iafd, "load_stock_actions", lambda _db: make_actions())
    monkeypatch.setattr(iafd, "load_first_trade", lambda _db: first)
    monkeypatch.setattr(iafd, "load_companies",
                        lambda _db: pd.DataFrame({"root": ROOTS, "cnpj": ROOTS}))

    days = pd.bdate_range("2020-01-01", "2022-06-30")
    tickers = [r + "3" for r in ROOTS]
    close = pd.DataFrame(10.0, index=days, columns=tickers)
    fin_vol = pd.DataFrame(1e7, index=days, columns=tickers)  # ~R$210mm/month
    ret = pd.DataFrame(0.0, index=pd.date_range("2020-01-31", "2022-06-30", freq="ME"),
                       columns=tickers)
    sector_pit = pd.DataFrame({"root": ["RETL", "GOOD"], "ref_date": pd.Timestamp("1900-01-01"),
                               "sector": ["Emp. Adm. Part. - Comércio (Atacado e Varejo)",
                                          "Máquinas, Equipamentos, Veículos e Peças"]})
    shared = {"ret": ret, "close_px": close, "fin_vol": fin_vol, "sector_pit": sector_pit}
    SHARED.update(shared)
    return iafd.TevaIAFDStrategy().generate_signals(shared, {})[1]


def test_rebalances_once_a_year_in_march(signals):
    live = signals.index[signals.abs().sum(axis=1) > 0]
    assert [d.strftime("%Y-%m") for d in live] == ["2020-03", "2021-03", "2022-03"]
    assert np.allclose(signals.loc[live].sum(axis=1), 1.0)


def test_selection_and_no_look_ahead(signals):
    mar20 = signals.loc["2020-03-31"]
    # RETL retail, LOSS losses, NEWW too young, LATE's passing restatement not
    # yet filed; BIGG is 3x GOOD's cap
    assert mar20[mar20 > 0].to_dict() == pytest.approx({"GOOD3": 0.25, "BIGG3": 0.75})
    mar21 = signals.loc["2021-03-31"]
    assert set(mar21[mar21 > 0].index) == {"GOOD3", "BIGG3", "LATE3", "NEWW3"}


def test_sector_roots_matches_cvm_labels_and_refuses_empty_register():
    sp = pd.DataFrame({"root": ["ITUB", "BBSE", "LREN", "XPTO"], "sector": [
        "Bancos", "Emp. Adm. Part. - Seguradoras e Corretoras",
        "Comércio (Atacado e Varejo)", "Comércio Exterior"]})
    assert iafd.sector_roots(sp, iafd.FINANCIAL_SECTORS) == {"ITUB", "BBSE"}
    assert iafd.sector_roots(sp, iafd.RETAIL_SECTORS) == {"LREN"}
    with pytest.raises(iafd.SectorRegistryEmpty):
        iafd.sector_roots(sp.head(0), iafd.RETAIL_SECTORS)


def test_empty_rebalance_after_inception_raises(monkeypatch, signals):
    # every name fails from FY2020 on: the 2022 rebalance has nothing to hold
    bad = iafd.load_dfp(None).assign(net_income=lambda d: d["net_income"].where(
        d["fiscal_year"] < 2020, -1.0))
    monkeypatch.setattr(iafd, "load_dfp", lambda _db: bad)
    with pytest.raises(RuntimeError, match="no company passed"):
        iafd.TevaIAFDStrategy().generate_signals(SHARED, {})


def test_strategy_is_registered():
    from backtests.core.strategy_registry import get_registry
    s = get_registry().get("TevaIAFD")
    assert {p.name for p in s.get_parameter_specs()} >= {"rebalance_month", "min_market_cap"}
