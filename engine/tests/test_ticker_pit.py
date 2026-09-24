"""CIK -> ticker with validity: the survivorship hole that was in the join.

Offline. The submissions documents are built in-process in the documented shape,
same convention as `test_sec_fundamentals.py`'s companyfacts fixtures.
"""
from __future__ import annotations

import pandas as pd
import pytest
from b3_pipeline import storage, us_storage
from us_pipeline import ticker_pit, us_main

from tests.test_sec_fundamentals import doc as _facts_doc
from tests.test_sec_fundamentals import fact
from tests.conftest import sqlite_db


def facts_doc(cik, **tags):
    """A companyfacts document for a specific filer — `parse_companyfacts` reads
    the CIK out of the document, not out of its caller."""
    out = _facts_doc(**tags)
    out["cik"] = int(cik)
    return out


def submission(cik, tickers, *, filings=("2005-03-01", "2009-09-15"),
               still_filing=True, exchanges=None):
    """A trimmed copy of the real `submissions.zip` member shape."""
    recent = {"filingDate": list(filings)}
    if still_filing:
        recent["form"] = ["10-K"] * len(filings)
    return {"cik": int(cik), "tickers": list(tickers),
            "exchanges": list(exchanges or []),
            "filings": {"recent": recent, "files": []}}


# ── Reading the archive ───────────────────────────────────────────────────────

def test_a_filer_that_stopped_gets_a_closed_interval():
    """The last filing date is the last day the market could have known the
    ticker. Not the delisting date — this source does not carry one, and
    `us_listing` owns that number."""
    rows = ticker_pit.intervals_from_submission(
        submission(806085, ["LEH"], still_filing=False))
    assert rows["ticker"].iloc[0] == "LEH"
    assert rows["start_date"].iloc[0] == "2005-03-01"
    assert rows["end_date"].iloc[0] == "2009-09-15"


def test_a_filer_that_is_still_filing_gets_an_open_interval():
    rows = ticker_pit.intervals_from_submission(submission(320193, ["AAPL"]))
    assert rows["end_date"].iloc[0] is None


def test_the_history_reaches_past_the_current_filing_page():
    """`filings.files` lists the older pages with their own ranges, and that is
    what reaches back far enough to bound a company that stopped decades ago."""
    doc = submission(806085, ["LEH"], still_filing=False)
    doc["filings"]["files"] = [{"filingFrom": "1994-01-05",
                                "filingTo": "2004-12-30"}]
    rows = ticker_pit.intervals_from_submission(doc)
    assert rows["start_date"].iloc[0] == "1994-01-05"


def test_an_entity_with_no_ticker_produces_nothing_rather_than_a_blank():
    assert ticker_pit.intervals_from_submission(submission(1, [])).empty


def test_multiple_tickers_keep_their_own_exchanges():
    rows = ticker_pit.intervals_from_submission(
        submission(1, ["GOOG", "GOOGL"], exchanges=["Nasdaq", "Nasdaq"]))
    assert list(rows["ticker"]) == ["GOOG", "GOOGL"]
    assert set(rows["exchange"]) == {"Nasdaq"}


# ── Resolving a date to a label ───────────────────────────────────────────────

def test_a_rename_gives_each_filing_the_name_the_market_knew_then(tmp_path):
    """`_query_fundamentals_pit` pivots on filing_date and forward-fills onto
    the rebalance grid, so the label has to be the one the market saw when the
    filing landed — otherwise the fundamentals column and the price column carry
    different names over the same window and never join."""
    conn = sqlite_db(tmp_path)
    us_storage.upsert_ticker_pit(conn, pd.DataFrame([
        {"cik": "0001326801", "ticker": "FB", "start_date": "2012-05-18",
         "end_date": "2022-06-08", "source": "sec-submissions"},
        {"cik": "0001326801", "ticker": "META", "start_date": "2022-06-09",
         "end_date": None, "source": "sec-submissions"}]))
    at = ticker_pit.resolver(conn, "0001326801")
    assert at("2015-02-01") == "FB"
    assert at("2023-02-01") == "META"


def test_a_filing_older_than_the_recorded_bound_takes_the_nearest_name(tmp_path):
    """The backward source is coarse — it bounds by filing history, not by
    trading — so a fact from before the earliest bound is normal. The nearest
    name beats no name, and `CIK##########` is no name."""
    conn = sqlite_db(tmp_path)
    us_storage.upsert_ticker_pit(conn, pd.DataFrame([
        {"cik": "0000806085", "ticker": "LEH", "start_date": "1994-01-05",
         "end_date": "2008-09-15", "source": "sec-submissions"}]))
    assert ticker_pit.resolver(conn, "0000806085")("1990-01-01") == "LEH"


def test_an_unknown_cik_resolves_to_nothing_rather_than_a_guess(tmp_path):
    assert ticker_pit.resolver(sqlite_db(tmp_path), "0000000001")("2020-01-01") is None


# ── The whole point ───────────────────────────────────────────────────────────

def test_a_company_that_stopped_filing_is_reachable_by_the_ticker_it_had(tmp_path):
    """#38's own acceptance criterion. Before this, its facts were in the
    database, /market/status counted them, and no strategy could select them —
    survivorship bias surviving despite the plane being built without it."""
    conn = sqlite_db(tmp_path)
    us_storage.upsert_ticker_pit(conn, ticker_pit.intervals_from_submission(
        submission(806085, ["LEH"], filings=("1994-01-05", "2008-09-15"),
                   still_filing=False)))

    # company_tickers.json has no entry for a company that stopped trading.
    us_main.ingest_company(
        conn, "0000806085",
        facts_doc(806085, Revenues=[fact(1e9, "2007-12-31", "a1", "2008-02-01",
                                         start="2007-01-01")]),
        ticker_pit.resolver(conn, "0000806085"))

    facts = storage.read_sql(
        "SELECT ticker FROM fundamentals_pit_us WHERE cik='0000806085'", conn)
    assert set(facts["ticker"]) == {"LEH"}
    symbol = storage.read_sql(
        "SELECT symbol FROM instruments WHERE cik='0000806085'", conn)
    assert symbol["symbol"].iloc[0] == "LEH"


def test_a_filer_with_no_history_at_all_keeps_the_cik_placeholder(tmp_path):
    """Still the honest fallback where nothing resolved — and its count is what
    `scripts/us_coverage.py` reports as the size of what is left."""
    conn = sqlite_db(tmp_path)
    us_main.ingest_company(
        conn, "0000000042",
        facts_doc(42, Revenues=[fact(1e6, "2007-12-31", "a1", "2008-02-01",
                                     start="2007-01-01")]),
        ticker_pit.resolver(conn, "0000000042"))
    symbol = storage.read_sql(
        "SELECT symbol FROM instruments WHERE cik='0000000042'", conn)
    assert symbol["symbol"].iloc[0] == "CIK0000000042"


# ── The forward snapshot ──────────────────────────────────────────────────────

def _snapshot(n, extra=None):
    m = {str(i).zfill(10): f"SYM{i}" for i in range(n)}
    m.update(extra or {})
    return m


def test_a_truncated_snapshot_does_not_end_every_interval_in_the_market(tmp_path):
    conn = sqlite_db(tmp_path)
    with pytest.raises(ValueError, match="below the"):
        ticker_pit.apply_snapshot(conn, _snapshot(10), "2026-09-01")


def test_a_pair_that_disappears_from_the_snapshot_is_closed(tmp_path):
    conn = sqlite_db(tmp_path)
    full = _snapshot(ticker_pit.MIN_PLAUSIBLE_PAIRS,
                     {"0000806085": "LEH"})
    ticker_pit.apply_snapshot(conn, full, "2026-09-01")
    assert ticker_pit.resolver(conn, "0000806085")("2026-09-01") == "LEH"

    del full["0000806085"]
    out = ticker_pit.apply_snapshot(conn, full, "2026-09-02")
    assert out["ended"] == 1
    row = us_storage.load_ticker_pit(conn, "0000806085").iloc[0]
    assert str(row["end_date"]) == "2026-09-02"
