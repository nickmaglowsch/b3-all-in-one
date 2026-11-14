"""The US instrument lifecycle: who may delist a symbol, who may revive it, and
the observed bounds no writer may move the wrong way.

The end-to-end assertions go through `symbol_directory.apply_snapshot` and the
backtest's own `tradable_mask`, because the bug being pinned was only visible
there: a relisted symbol that the plane went on treating as dead.
"""
from __future__ import annotations

import datetime
import os
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage
from backtests.core import universe
from us_pipeline import symbol_directory as sd
from us_pipeline import us_lifecycle as lc
from tests.conftest import sqlite_db
from tests.test_symbol_directory import directory, many

D = datetime.date


def _row(conn, symbol, market="US"):
    return storage.read_sql(
        "SELECT * FROM instruments WHERE market = ? AND symbol = ?",
        conn, params=(market, symbol)).iloc[0]


def _snap(conn, symbols, day):
    return sd.apply_snapshot(conn, sd.parse_directory(directory(symbols)), day)


# ── the directory revives what it delisted ────────────────────────────────────

def test_a_relisted_symbol_is_revived_with_its_first_sighting_intact(tmp_path):
    """It used to be read as a first sighting: `first_seen` reset to the day it
    came back, and the old `delisting_date` kept."""
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    _snap(conn, syms, "2026-01-05")
    _snap(conn, syms[1:], "2026-01-06")
    assert _row(conn, syms[0])["delisting_date"] == "2026-01-05"

    out = _snap(conn, syms, "2026-01-07")
    row = _row(conn, syms[0])
    assert out["revived"] == 1
    assert pd.isna(row["delisting_date"]) and pd.isna(row["delist_reason"])
    assert row["first_seen"] == "2026-01-05"
    assert row["last_seen"] == "2026-01-07"


def test_a_relisted_symbol_is_tradable_again_in_a_backtest(tmp_path):
    """What the stale delisting cost: from its old date on, `tradable_mask`
    excluded a symbol that was trading."""
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    _snap(conn, syms, "2026-01-05")
    _snap(conn, syms[1:], "2026-01-06")
    _snap(conn, syms, "2026-01-07")
    conn.close()

    intervals = universe.load_listing_intervals(
        str(tmp_path / "engine.sqlite"), "US")
    mask = universe.tradable_mask(intervals, ["2026-01-08"], [syms[0]])
    assert bool(mask.iloc[0, 0])


def test_a_ticker_back_after_the_relist_window_is_a_different_company(tmp_path):
    """Delisted in 2021, back in 2025: a recycled ticker, not a missed capture.
    Reviving it would merge the two companies — the old one's delisting (and its
    terminal return) erased, its last price carried across four years."""
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    _snap(conn, syms, "2021-03-01")
    _snap(conn, syms[1:], "2021-03-02")

    out = _snap(conn, syms, "2025-06-01")
    row = _row(conn, syms[0])
    assert out["revived"] == 0
    assert row["delisting_date"] == "2021-03-01"
    assert row["delist_reason"] == lc.DIRECTORY_REASON
    assert row["last_seen"] == "2021-03-01"  # the old company's last day


def test_a_symbol_back_inside_the_relist_window_is_revived():
    prior = {"delisting_date": "2026-01-05", "delist_reason": lc.DIRECTORY_REASON}
    assert "delisting_date" in lc.observe_present(prior, D(2026, 2, 4))
    assert "delisting_date" not in lc.observe_present(prior, D(2026, 2, 5))


def test_the_directory_does_not_revive_a_delisting_the_vendor_claimed(tmp_path):
    """Withdrawing `us_listing`'s claim is `us_listing`'s call, on its own
    evidence; a sighting here only moves the observed bounds."""
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    _snap(conn, syms, "2026-01-05")
    market_storage.upsert_instruments(conn, pd.DataFrame([{
        "market": "US", "symbol": syms[0], "currency": "USD",
        "delisting_date": "2026-01-05", "delist_reason": lc.LISTING_REASON}]))

    _snap(conn, syms, "2026-01-06")
    row = _row(conn, syms[0])
    assert row["delisting_date"] == "2026-01-05"
    assert row["last_seen"] == "2026-01-06"


def test_absence_of_a_symbol_the_directory_never_saw_proves_nothing(tmp_path):
    """A row another writer created — a SEC filer, a name delisted before
    capture began — has no `last_seen`. Its absence used to write a delisting
    reason with no delisting date."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "US", "symbol": "FILER", "currency": "USD", "cik": "1"}]))
    out = _snap(conn, many(6_000), "2026-01-05")
    row = _row(conn, "FILER")
    assert out["delisted"] == 0
    assert pd.isna(row["delisting_date"]) and pd.isna(row["delist_reason"])


# ── the transitions, pure ─────────────────────────────────────────────────────

LIVE = {"last_seen": D(2011, 6, 10), "delisting_date": None, "delist_reason": None}


def test_absence_dates_the_delisting_at_the_last_sighting():
    assert lc.observe_absent(LIVE) == {
        "delisting_date": D(2011, 6, 10), "delist_reason": lc.DIRECTORY_REASON}


def test_absence_of_an_already_dead_symbol_changes_nothing():
    dead = {**LIVE, "delisting_date": D(2011, 6, 10),
            "delist_reason": lc.DIRECTORY_REASON}
    assert lc.observe_absent(dead) is None
    assert lc.observe_absent(None) is None


def test_a_claim_is_never_written_over_another_writers_verdict():
    dead = {**LIVE, "delisting_date": D(2011, 6, 10),
            "delist_reason": lc.DIRECTORY_REASON}
    assert lc.claim_delisted(dead, D(2015, 1, 1)) == (None, "skip")


def test_a_claim_before_watched_trading_is_a_recycled_ticker():
    assert lc.claim_delisted(LIVE, D(2001, 1, 1)) == (None, "conflict")


def test_a_claim_inside_settlement_slack_takes_the_observed_day():
    change, verdict = lc.claim_delisted(LIVE, D(2011, 6, 8))
    assert verdict == "write" and change["delisting_date"] == D(2011, 6, 10)
    assert change["delist_reason"] == lc.LISTING_REASON


def test_a_claim_about_a_symbol_the_plane_never_held_is_written():
    change, verdict = lc.claim_delisted(None, D(2009, 1, 2))
    assert verdict == "write" and change["delisting_date"] == D(2009, 1, 2)


def test_a_claim_with_no_date_is_skipped():
    assert lc.claim_delisted(LIVE, None) == (None, "skip")


def test_only_the_vendors_own_claim_is_withdrawn():
    own = {**LIVE, "delisting_date": D(2011, 1, 1),
           "delist_reason": lc.LISTING_REASON}
    watched = {**own, "delist_reason": lc.DIRECTORY_REASON}
    assert lc.withdraw_claim(own) == lc.CLEARED
    assert lc.withdraw_claim(watched) is None
    assert lc.withdraw_claim(LIVE) is None


# ── the observed bounds only widen, whoever writes them ───────────────────────

def _bounds(conn, **extra):
    market_storage.upsert_instruments(conn, pd.DataFrame([{
        "market": "US", "symbol": "AAA", "currency": "USD", **extra}]))
    row = _row(conn, "AAA")
    return row["first_seen"], row["last_seen"]


def test_last_seen_never_moves_back_and_first_seen_never_forward(tmp_path):
    conn = sqlite_db(tmp_path)
    _bounds(conn, first_seen="2020-01-01", last_seen="2026-09-01")
    assert _bounds(conn, first_seen="2021-01-01", last_seen="2018-03-27") == (
        "2020-01-01", "2026-09-01")
    assert _bounds(conn, first_seen="1999-01-04", last_seen="2026-09-02") == (
        "1999-01-04", "2026-09-02")


def test_an_unknown_bound_never_erases_a_known_one(tmp_path):
    conn = sqlite_db(tmp_path)
    _bounds(conn, first_seen="2020-01-01", last_seen="2026-09-01")
    assert _bounds(conn, first_seen=None, last_seen=None) == (
        "2020-01-01", "2026-09-01")


def test_a_first_bound_lands_on_a_row_that_had_none(tmp_path):
    conn = sqlite_db(tmp_path)
    _bounds(conn)
    assert _bounds(conn, first_seen="2020-01-01", last_seen="2020-02-01") == (
        "2020-01-01", "2020-02-01")


def test_crypto_can_still_revive_a_pair_by_clearing_its_delisting(tmp_path):
    """`crypto_main` writes `delisting_date=None` on purpose when a pair's
    klines resume; the bounds rule must not reach the delisting columns."""
    conn = sqlite_db(tmp_path)
    base = {"market": "CRYPTO", "symbol": "SRMUSDT", "currency": "USDT"}
    market_storage.upsert_instruments(conn, pd.DataFrame([{
        **base, "delisting_date": "2023-06-01", "delist_reason": "archive ends",
        "first_seen": "2020-08-11", "last_seen": "2023-06-01"}]))
    market_storage.upsert_instruments(conn, pd.DataFrame([{
        **base, "delisting_date": None, "delist_reason": None,
        "last_seen": "2026-09-01"}]))
    row = _row(conn, "SRMUSDT", market="CRYPTO")
    assert pd.isna(row["delisting_date"])
    assert row["last_seen"] == "2026-09-01" and row["first_seen"] == "2020-08-11"


# ── the same rule on Postgres ─────────────────────────────────────────────────

def _swap_db(url, dbname):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + dbname,
                       parts.query, parts.fragment))


@pytest.mark.postgres
@pytest.mark.skipif(not storage.using_postgres(),
                    reason="ENGINE_DATABASE_URL is not a postgresql:// DSN")
def test_the_bounds_rule_holds_on_postgres(monkeypatch):
    """The CASE is written once for both backends; this is the proof it means
    the same thing on the one CI does not run by default."""
    import psycopg

    base = os.environ["ENGINE_DATABASE_URL"]
    admin, name = _swap_db(base, "postgres"), "engine_test_us_lifecycle"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}")
        c.execute(f"CREATE DATABASE {name}")
    try:
        monkeypatch.setenv("ENGINE_DATABASE_URL", _swap_db(base, name))
        with storage.get_connection() as conn:
            storage.init_db(conn)
            _bounds(conn, first_seen="2020-01-01", last_seen="2026-09-01")
            first, last = _bounds(conn, first_seen="2021-01-01",
                                  last_seen="2018-03-27")
            assert (str(first), str(last)) == ("2020-01-01", "2026-09-01")
            first, last = _bounds(conn, first_seen=None, last_seen="2026-09-02")
            assert (str(first), str(last)) == ("2020-01-01", "2026-09-02")
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {name}")
