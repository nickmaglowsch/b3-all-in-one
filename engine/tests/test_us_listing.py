"""LISTING_STATUS -> instruments: the backward half of the US listing record.

Offline. The two listing files are built in-process in the documented shape;
what is under test is the reconciliation against what `symbol_directory` already
wrote, and the three refusals that stop a bad snapshot before it touches
anything.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage
from us_pipeline import us_listing


def _db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "listing.sqlite"))
    storage.init_db(conn)
    return conn


def _rows(n, prefix="SYM", status="active", ipo="2000-01-01", delisted=None):
    return pd.DataFrame([{
        "symbol": f"{prefix}{i}", "name": f"{prefix} {i} Inc",
        "venue": "NASDAQ", "kind": "EQUITY",
        "ipo_date": datetime.date.fromisoformat(ipo),
        "delisting_date": datetime.date.fromisoformat(delisted) if delisted else pd.NaT,
        "status": status,
    } for i in range(n)])


def _plausible_active(n=us_listing.MIN_PLAUSIBLE_ACTIVE):
    return _rows(n, "ACT")


def _seen_by_the_directory(conn, symbol, first, last, **extra):
    """What symbol_directory would have written for a symbol it watched."""
    market_storage.upsert_instruments(conn, pd.DataFrame([{
        "market": "US", "symbol": symbol, "currency": "USD",
        "name": f"{symbol} Corp", "venue": "NYSE", "kind": "EQUITY",
        "first_seen": first, "last_seen": last, **extra}]))


def _instrument(conn, symbol):
    return storage.read_sql(
        "SELECT * FROM instruments WHERE market='US' AND symbol = ?",
        conn, params=(symbol,)).iloc[0]


# ── The refusals ──────────────────────────────────────────────────────────────

def test_a_truncated_active_file_is_refused_and_writes_nothing(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="below the"):
        us_listing.reconcile(conn, _rows(10, "ACT"), _rows(3, "DEAD",
                                                           delisted="2010-01-01"))
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 0


def test_an_empty_delisted_set_is_a_truncation_signature(tmp_path):
    """The delisted set is tens of thousands of rows and never empty."""
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="no delisted"):
        us_listing.reconcile(conn, _plausible_active(), _rows(0, "DEAD"))


def test_a_mass_delisting_is_refused_rather_than_applied(tmp_path):
    """A real day adds delistings in single digits."""
    conn = _db(tmp_path)
    for i in range(200):
        _seen_by_the_directory(conn, f"LIVE{i}", "2026-01-05", "2026-08-31")
    dead = _rows(200, "LIVE", status="delisted", delisted="2026-08-31")
    with pytest.raises(ValueError, match="ceiling"):
        us_listing.reconcile(conn, _plausible_active(), dead)
    assert pd.isna(_instrument(conn, "LIVE0")["delisting_date"])


# ── What it writes, and what it refuses to touch ──────────────────────────────

def test_a_company_that_died_before_capture_began_finally_gets_an_interval(tmp_path):
    """The whole point. `universe` treats an instrument with no interval as
    tradable on every date, so a 2008 casualty with no delisting_date is exactly
    the survivorship bias the plane exists to remove."""
    conn = _db(tmp_path)
    dead = _rows(1, "LEH", status="delisted", ipo="1994-01-01",
                 delisted="2008-09-15")
    us_listing.reconcile(conn, _plausible_active(), dead)
    row = _instrument(conn, "LEH0")
    assert str(row["listing_date"]) == "1994-01-01"
    assert str(row["delisting_date"]) == "2008-09-15"
    # Nobody else will ever describe a symbol that stopped trading before
    # capture began, so this writer supplies the descriptive columns too.
    assert row["name"] == "LEH 0 Inc"
    assert row["venue"] == "NASDAQ"


def test_the_observed_bounds_are_never_restated_by_a_claim(tmp_path):
    """`first_seen`/`last_seen` are observed in the market and belong to the
    directory; this feed writes intervals and nothing else for a symbol the
    directory has seen."""
    conn = _db(tmp_path)
    _seen_by_the_directory(conn, "ACT0", "2026-01-05", "2026-08-31")
    us_listing.reconcile(conn, _plausible_active(), _rows(1, "DEAD",
                                                          status="delisted",
                                                          delisted="2010-01-01"))
    row = _instrument(conn, "ACT0")
    assert str(row["first_seen"]) == "2026-01-05"
    assert str(row["last_seen"]) == "2026-08-31"
    assert row["name"] == "ACT0 Corp"      # the directory's name, not this feed's
    assert row["venue"] == "NYSE"
    assert str(row["listing_date"]) == "2000-01-01"   # and the interval landed


def test_a_delisting_is_never_dated_before_a_day_the_plane_saw_it_trade(tmp_path):
    """Settlement date versus last trade. The observed day is the one the plane
    can prove."""
    conn = _db(tmp_path)
    _seen_by_the_directory(conn, "DEAD0", "2026-01-05", "2026-08-31")
    us_listing.reconcile(conn, _plausible_active(),
                         _rows(1, "DEAD", status="delisted", delisted="2026-08-28"))
    assert str(_instrument(conn, "DEAD0")["delisting_date"]) == "2026-08-31"


def test_a_claim_that_predates_recorded_trading_is_a_conflict_not_a_merge(tmp_path):
    """A ticker recycled onto a new company. Merging the two would put one
    company's fundamentals on the other's prices, so nothing is written."""
    conn = _db(tmp_path)
    _seen_by_the_directory(conn, "DEAD0", "2026-01-05", "2026-08-31")
    out = us_listing.reconcile(
        conn, _plausible_active(),
        _rows(1, "DEAD", status="delisted", delisted="2011-04-01"))
    assert out["conflict_count"] == 1
    assert "DEAD0" in out["conflicts"]
    assert pd.isna(_instrument(conn, "DEAD0")["delisting_date"])


def test_an_active_claim_does_not_resurrect_a_name_the_directory_watched_die(tmp_path):
    """Absence from the consolidated directory is evidence; a stale vendor
    snapshot is not."""
    conn = _db(tmp_path)
    _seen_by_the_directory(
        conn, "ACT0", "2026-01-05", "2026-06-30",
        delisting_date="2026-06-30",
        delist_reason="absent from the consolidated symbol directory")
    us_listing.reconcile(conn, _plausible_active(),
                         _rows(1, "DEAD", status="delisted", delisted="2010-01-01"))
    row = _instrument(conn, "ACT0")
    assert str(row["delisting_date"]) == "2026-06-30"
    assert row["delist_reason"] == "absent from the consolidated symbol directory"


def test_this_module_corrects_its_own_earlier_claims(tmp_path):
    """Self-correction is the one case where an active row clears a delisting:
    the previous claim came from here, so a newer one supersedes it."""
    conn = _db(tmp_path)
    # First run: the vendor reports GONE0 as delisted and it is not in the
    # active file, so this module records the claim.
    us_listing.reconcile(conn, _plausible_active(),
                         _rows(1, "GONE", status="delisted", delisted="2020-01-01"))
    assert str(_instrument(conn, "GONE0")["delisting_date"]) == "2020-01-01"
    # Second run: the vendor now reports it trading. Its own claim, so it goes.
    revived = pd.concat([_plausible_active(), _rows(1, "GONE")], ignore_index=True)
    us_listing.reconcile(conn, revived,
                         _rows(1, "DEAD", status="delisted", delisted="2010-01-01"))
    row = _instrument(conn, "GONE0")
    assert pd.isna(row["delisting_date"])
    assert row["delist_reason"] is None


def test_a_delisted_row_with_no_date_is_skipped_rather_than_guessed(tmp_path):
    conn = _db(tmp_path)
    dead = _rows(1, "DEAD", status="delisted")
    out = us_listing.reconcile(conn, _plausible_active(), dead)
    assert out["conflict_count"] == 0
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM instruments WHERE symbol='DEAD0'",
        conn).iloc[0]["c"] == 0


def test_withdrawing_a_claim_does_not_touch_the_symbols_around_it(tmp_path):
    """Clearing one delisting must not clear five thousand. The rows carry
    different columns, so they cannot share one upsert."""
    conn = _db(tmp_path)
    _seen_by_the_directory(conn, "ACT0", "2026-01-05", "2026-08-31")
    us_listing.reconcile(conn, _plausible_active(),
                         _rows(1, "GONE", status="delisted", delisted="2020-01-01"))
    out = us_listing.reconcile(
        conn, pd.concat([_plausible_active(), _rows(1, "GONE")], ignore_index=True),
        _rows(1, "DEAD", status="delisted", delisted="2010-01-01"))
    assert out["revived"] == 1
    survivor = _instrument(conn, "ACT0")
    assert survivor["name"] == "ACT0 Corp"
    assert str(survivor["first_seen"]) == "2026-01-05"
    assert str(_instrument(conn, "DEAD0")["delisting_date"]) == "2010-01-01"
