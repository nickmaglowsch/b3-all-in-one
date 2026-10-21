"""Point-in-time fundamentals: TTM earnings, restatements of old periods, and
share counts on the close's basis (engine audit M1, M3, M4).

* CVM ITR income is year-to-date. P/E and EV/EBITDA built on it were ~4x too
  high after Q1 filings, ~2x after Q2 — the reporting calendar, not the price,
  decided who passed a P/E band.
* A late restatement of an *older* quarter used to become the visible value
  just because it was filed last, replacing the newer quarter's numbers.
* Share counts up to 400 days old were multiplied by the unadjusted close of
  the rebalance date; after a 1:10 split the company was a tenth of its size.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import storage
from backtests.core.data import (
    _pit_frame,
    latest_period_rows,
    split_multiplier_frame,
    ttm_from_ytd,
)


# ── TTM from year-to-date ─────────────────────────────────────────────────────

def _ytd_rows():
    return pd.DataFrame([
        # cnpj, doc_type, fy, q, period_end, filing_date, version, ebitda
        ("X", "ITR", 2022, 1, "2022-03-31", "2022-05-10", 1, 150.0),
        ("X", "ITR", 2022, 2, "2022-06-30", "2022-08-10", 1, 380.0),
        ("X", "DFP", 2022, None, "2022-12-31", "2023-03-01", 1, 800.0),
        ("X", "ITR", 2023, 1, "2023-03-31", "2023-05-10", 1, 250.0),
        ("X", "ITR", 2023, 2, "2023-06-30", "2023-08-10", 1, 450.0),
        ("X", "FRE", 2023, None, "2023-01-01", "2023-05-30", 1, np.nan),
    ], columns=["cnpj", "doc_type", "fiscal_year", "quarter", "period_end",
                "filing_date", "filing_version", "ebitda"]).assign(ticker="XXXX")


def test_ttm_from_ytd_sums_the_last_four_quarters():
    ttm = ttm_from_ytd(_ytd_rows(), "ebitda")
    # 2022 quarters have no prior year: no TTM rather than a YTD posing as one.
    assert ttm.iloc[0] != ttm.iloc[0] and ttm.iloc[1] != ttm.iloc[1]
    assert ttm.iloc[2] == 800.0                          # DFP = the year
    assert ttm.iloc[3] == pytest.approx(250 + 800 - 150)  # Q1 2023
    assert ttm.iloc[4] == pytest.approx(450 + 800 - 380)  # Q2 2023
    assert np.isnan(ttm.iloc[5])                          # not a statement


def test_ttm_components_are_as_of_the_rows_own_filing_date():
    rows = _ytd_rows()
    # The 2022 DFP restated *after* Q1 2023 was filed: Q1 2023's TTM, stamped
    # 2023-05-10, must use the annual figure known then (800), not 900.
    rows.loc[len(rows)] = ["X", "DFP", 2022, None, "2022-12-31", "2023-06-01", 2,
                           900.0, "XXXX"]
    ttm = ttm_from_ytd(rows, "ebitda")
    assert ttm.iloc[3] == pytest.approx(250 + 800 - 150)
    # Q2 2023, filed after the restatement, sees it.
    assert ttm.iloc[4] == pytest.approx(450 + 900 - 380)


# ── Restatement of an older period ────────────────────────────────────────────

def _restated():
    df = pd.DataFrame([
        dict(ticker="ABCD", filing_date="2021-05-14", period_end="2021-03-31",
             filing_version=1, equity=100.0),
        dict(ticker="ABCD", filing_date="2021-08-13", period_end="2021-06-30",
             filing_version=1, equity=200.0),
        # Q1 re-filed (v2) after Q2: history, not the current balance.
        dict(ticker="ABCD", filing_date="2021-09-10", period_end="2021-03-31",
             filing_version=2, equity=101.0),
        # Q2 re-filed: the current period, so it does replace Q2 v1.
        dict(ticker="ABCD", filing_date="2021-10-20", period_end="2021-06-30",
             filing_version=2, equity=202.0),
    ])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df


def test_a_late_restatement_of_an_older_quarter_does_not_replace_a_newer_one():
    grid = pd.date_range("2021-04-30", "2021-12-31", freq="ME")
    wide = _pit_frame(_restated(), "equity", grid, "2021-01-01", 400)["ABCD"]
    assert wide.loc["2021-05-31"] == 100.0
    assert wide.loc["2021-08-31"] == 200.0
    assert wide.loc["2021-09-30"] == 200.0        # was 101.0 — the stale Q1
    assert wide.loc["2021-10-31"] == 202.0        # Q2's own restatement wins


def test_other_document_families_do_not_compete_on_period_end():
    # An FRE's period_end is a form reference date. A share count from an FRE
    # filed after the Q1 ITR must still show, though its period_end is older.
    df = pd.DataFrame([
        dict(ticker="ABCD", filing_date="2021-05-14", period_end="2021-03-31",
             filing_version=1, doc_type="ITR", shares_outstanding=1000.0),
        dict(ticker="ABCD", filing_date="2021-05-31", period_end="2021-01-01",
             filing_version=1, doc_type="FRE", shares_outstanding=2000.0),
    ])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    kept = latest_period_rows(df)
    assert len(kept) == 2
    grid = pd.date_range("2021-05-31", periods=2, freq="ME")
    wide = _pit_frame(df, "shares_outstanding", grid, "2021-01-01", 400)["ABCD"]
    assert wide.loc["2021-06-30"] == 2000.0


def test_sp500_b3_snapshot_skips_the_stale_restatement():
    from backtests.strategies.sp500_b3 import pit_snapshot

    fund = _restated()
    fund["period_end"] = pd.to_datetime(fund["period_end"])
    fund["net_income_ttm"] = 10.0
    fund["shares_outstanding"] = 1.0
    snap = pit_snapshot(fund, pd.Timestamp("2021-09-30")).set_index("ticker")
    assert snap.loc["ABCD", "equity"] == 200.0


# ── Share counts on the close's basis ─────────────────────────────────────────

def test_split_multiplier_frame_counts_actions_between_anchor_and_date():
    grid = pd.date_range("2023-04-30", "2023-08-31", freq="ME")
    day = pd.Timedelta(days=1)
    q1 = (pd.Timestamp("2023-03-31") - pd.Timestamp("1970-01-01")) / day
    anchor = pd.DataFrame({"PETR4": q1, "VALE3": np.nan, "ITUB4": q1}, index=grid)
    actions = pd.DataFrame([
        ("PETR4", "2023-06-15", "STOCK_SPLIT", 10.0),
        ("PETR4", "2023-02-01", "STOCK_SPLIT", 2.0),     # before the anchor
        ("VALE3", "2023-06-15", "STOCK_SPLIT", 10.0),    # no anchor -> 1.0
        ("ITUB4", "2023-07-03", "BONUS_SHARES", 10.0),   # +10%
    ], columns=["ticker", "ex_date", "action_type", "factor"])
    actions["ex_date"] = pd.to_datetime(actions["ex_date"])
    m = split_multiplier_frame(actions, anchor)
    assert m["PETR4"].tolist() == pytest.approx([1, 1, 10, 10, 10])
    assert (m["VALE3"] == 1.0).all()
    assert m["ITUB4"].tolist() == pytest.approx([1, 1, 1, 1.1, 1.1])


# ── Through fundamentals.attach, on a real (SQLite) plane ─────────────────────

@pytest.fixture
def plane(tmp_path):
    db = str(tmp_path / "f.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    for d in pd.date_range("2022-01-01", "2023-12-31", freq="B"):
        conn.execute(
            "INSERT INTO prices (ticker, isin_code, date, close, volume)"
            " VALUES ('PETR4', 'BRPETRACNPR6', ?, 10, 1000)", (d.strftime("%Y-%m-%d"),))
    conn.execute("INSERT INTO stock_actions (isin_code, ex_date, action_type, factor)"
                 " VALUES ('BRPETRACNPR6', '2023-06-15', 'STOCK_SPLIT', 10.0)")
    pit = [
        # id, doc, fy, q, period_end, filing_date, ni, ebitda, shares
        ("a", "ITR", 2022, 1, "2022-03-31", "2022-05-10", 50.0, 150.0, 1000.0),
        ("b", "DFP", 2022, None, "2022-12-31", "2023-03-01", 400.0, 800.0, 1000.0),
        ("c", "ITR", 2023, 1, "2023-03-31", "2023-05-10", 100.0, 250.0, 1000.0),
    ]
    for fid, doc, fy, q, pe, fd, ni, eb, sh in pit:
        conn.execute(
            "INSERT INTO fundamentals_pit (filing_id, cnpj, ticker, period_end,"
            " filing_date, filing_version, doc_type, fiscal_year, quarter,"
            " net_income, ebitda, equity, net_debt, shares_outstanding)"
            " VALUES (?, 'X', 'PETR', ?, ?, 1, ?, ?, ?, ?, ?, 1000.0, 0.0, ?)",
            (fid, pe, fd, doc, fy, q, ni, eb, sh))
    # The materialized monthly snapshot: YTD income 100, TTM 450.
    for me in ("2023-05-31", "2023-06-30", "2023-07-31"):
        conn.execute(
            "INSERT INTO fundamentals_monthly (month_end, ticker, net_income,"
            " net_income_ttm, ebitda, equity, net_debt, shares_outstanding)"
            " VALUES (?, 'PETR4', 100.0, 450.0, 250.0, 1000.0, 0.0, 1000.0)", (me,))
    conn.commit()
    conn.close()
    return db


def _attach(db):
    from backtests.core import fundamentals

    grid = pd.date_range("2023-01-31", "2023-07-31", freq="ME")
    ret = pd.DataFrame(0.0, index=grid, columns=["PETR4"])
    # Unadjusted close: R$10 before the 1:10 split, R$1 after.
    raw = pd.DataFrame({"PETR4": [10, 10, 10, 10, 10, 1, 1]}, index=grid, dtype=float)
    shared = {"raw_close": raw}
    fundamentals.attach(shared, db, "2023-01-01", "2023-07-31", "ME", ret, "B3")
    return shared


def test_pe_uses_ttm_income_not_year_to_date(plane):
    shared = _attach(plane)
    pe = shared["f_pe_ratio_dyn"].loc["2023-05-31", "PETR4"]
    # market cap 10 x 1000 over TTM 450 (x1000 BRL) — not over Q1's YTD 100.
    assert pe == pytest.approx(10 * 1000 / (450 * 1000))


def test_ev_ebitda_uses_ttm_ebitda(plane):
    shared = _attach(plane)
    assert shared["f_ebitda_ttm"].loc["2023-05-31", "PETR4"] == pytest.approx(900.0)
    ev = shared["f_ev_ebitda_dyn"].loc["2023-05-31", "PETR4"]
    assert ev == pytest.approx(10 * 1000 / (900 * 1000))


def test_market_cap_survives_a_split_after_the_share_count_was_filed(plane):
    shared = _attach(plane)
    shares = shared["f_shares_outstanding_m"]["PETR4"]
    assert shares.loc["2023-05-31"] == 1000.0
    assert shares.loc["2023-07-31"] == pytest.approx(10_000.0)   # post-split basis
    pe = shared["f_pe_ratio_dyn"]["PETR4"]
    # Same company, same earnings, same value: the split must not move P/E.
    assert pe.loc["2023-07-31"] == pytest.approx(pe.loc["2023-05-31"])
    # The PIT broadcast frame book_to_price_pit reads gets the same correction.
    assert shared["f_shares_outstanding"].loc["2023-07-31", "PETR4"] == pytest.approx(10_000.0)
