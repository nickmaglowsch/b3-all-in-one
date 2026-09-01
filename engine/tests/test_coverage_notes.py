"""The caveats a backtest carries in its own result.

The spec commits the platform to reporting its gaps as loudly as its results,
and until this existed the US plane had no channel to do it through: a window
opening before the price archive did returned a number that looked exactly as
clean as one that did not.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
from b3_pipeline import market_storage, storage, us_storage
from backtests.core import universe


def _plane(tmp_path, prices=(), instruments=(), cursor=()):
    path = tmp_path / "cov.sqlite"
    conn = sqlite3.connect(str(path))
    storage.init_db(conn)
    if instruments:
        market_storage.upsert_instruments(conn, pd.DataFrame(list(instruments)))
    if prices:
        market_storage.upsert_market_prices(conn, pd.DataFrame(list(prices)))
    if cursor:
        us_storage.upsert_price_cursor(conn, pd.DataFrame(list(cursor)))
    conn.commit()
    conn.close()
    return str(path)


def _bar(date, **kw):
    row = {"market": "US", "symbol": "AAA", "date": date, "open": 1.0,
           "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
           "adj_close": 1.0, "close_brl": 5.0, "adj_close_brl": 5.0,
           "fx_rate": 5.0, "source": "test"}
    row.update(kw)
    return row


def _codes(notes) -> set:
    return {n["code"] for n in notes}


def test_a_window_that_opens_before_the_archive_says_so(tmp_path):
    path = _plane(tmp_path, prices=[_bar("2024-01-02"), _bar("2024-01-03")])
    notes = universe.coverage_notes(path, "US", "2010-01-01", "2024-12-31")
    assert "price_coverage_starts_late" in _codes(notes)
    detail = next(n for n in notes
                  if n["code"] == "price_coverage_starts_late")["detail"]
    assert detail == {"requested_start": "2010-01-01",
                      "coverage_start": "2024-01-02", "market": "US"}


def test_a_window_inside_the_archive_says_nothing_about_it(tmp_path):
    path = _plane(tmp_path, prices=[_bar("2024-01-02")])
    assert "price_coverage_starts_late" not in _codes(
        universe.coverage_notes(path, "US", "2024-06-01", "2024-12-31"))


def test_a_new_year_start_is_not_a_coverage_gap(tmp_path):
    """"2024-01-01" is how everyone writes a year, and the first session is the
    2nd. Warning there would train people to ignore the warning."""
    path = _plane(tmp_path, prices=[_bar("2024-01-02")])
    assert "price_coverage_starts_late" not in _codes(
        universe.coverage_notes(path, "US", "2024-01-01", "2024-12-31"))


def test_instruments_with_no_interval_are_the_survivorship_warning(tmp_path):
    """`universe` treats an instrument with no interval as tradable on every
    date — the permissive default, chosen deliberately, and exactly the bias a
    long backtest is trying to avoid."""
    path = _plane(tmp_path, instruments=[
        {"market": "US", "symbol": "AAA", "currency": "USD"},
        {"market": "US", "symbol": "BBB", "currency": "USD",
         "listing_date": "2000-01-01", "delisting_date": "2011-04-01"}])
    detail = next(n for n in universe.coverage_notes(path, "US", "2010-01-01",
                                                     "2024-12-31")
                  if n["code"] == "listing_intervals_incomplete")["detail"]
    assert detail["instruments"] == 2 and detail["without_interval"] == 1


def test_unconverted_bars_are_counted_in_the_window(tmp_path):
    path = _plane(tmp_path, prices=[
        _bar("2024-01-02"),
        _bar("2024-01-03", close_brl=None, adj_close_brl=None, fx_rate=None)])
    detail = next(n for n in universe.coverage_notes(path, "US", "2024-01-01",
                                                     "2024-12-31")
                  if n["code"] == "unconverted_brl")["detail"]
    assert detail["rows"] == 1


def test_a_partly_adjusted_us_plane_says_which_symbols_are_which(tmp_path):
    path = _plane(tmp_path, prices=[_bar("2024-01-02")], cursor=[
        {"symbol": "AAA", "adjust_state": "total_return"},
        {"symbol": "BBB", "adjust_state": "unadjusted"}])
    detail = next(n for n in universe.coverage_notes(path, "US", "2024-01-01",
                                                     "2024-12-31")
                  if n["code"] == "adj_close_partial")["detail"]
    assert detail["by_state"] == {"total_return": 1, "unadjusted": 1}


def test_a_fully_adjusted_plane_raises_no_adjustment_caveat(tmp_path):
    path = _plane(tmp_path, prices=[_bar("2024-01-02")],
                  cursor=[{"symbol": "AAA", "adjust_state": "total_return"}])
    assert "adj_close_partial" not in _codes(
        universe.coverage_notes(path, "US", "2024-01-01", "2024-12-31"))


def test_the_notes_are_codes_not_sentences(tmp_path):
    """The frontend owns the pt-BR copy; a message built in the engine would be
    an English sentence in a Portuguese product."""
    path = _plane(tmp_path, prices=[_bar("2024-01-02")])
    for note in universe.coverage_notes(path, "US", "2010-01-01", "2024-12-31"):
        assert set(note) == {"code", "detail"}
        assert note["code"].islower() and " " not in note["code"]


def test_an_older_plane_degrades_to_no_notes_rather_than_failing(tmp_path):
    """Same posture as `load_listing_intervals`: a caveat that cannot be
    computed is not a caveat, and it must not cost a backtest its run."""
    empty = tmp_path / "bare.sqlite"
    sqlite3.connect(str(empty)).close()
    assert universe.coverage_notes(str(empty), "US", "2010-01-01",
                                   "2024-12-31") == []


def test_a_clean_plane_reports_nothing(tmp_path):
    path = _plane(tmp_path, prices=[_bar("2024-01-02")],
                  instruments=[{"market": "US", "symbol": "AAA",
                                "currency": "USD", "listing_date": "2020-01-01"}],
                  cursor=[{"symbol": "AAA", "adjust_state": "total_return"}])
    assert universe.coverage_notes(path, "US", "2024-01-01", "2024-12-31") == []
