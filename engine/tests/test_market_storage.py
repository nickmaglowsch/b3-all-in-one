"""Tests for the multi-market plane: `instruments`, `market_prices`,
`fundamentals_pit_us`.

Everything here is synthetic SQLite in a tmp_path, like the rest of the engine
suite. Nothing reaches the network — the archives these tables are fed from
(Binance, SEC) are exercised by their own parser tests against vendored fixtures.
"""
from __future__ import annotations

import pandas as pd
import pytest

from b3_pipeline import market_storage, storage, us_storage
from tests.conftest import sqlite_db


def _table_names(conn) -> set:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in cur.fetchall()}


# ── Schema ────────────────────────────────────────────────────────────────────

def test_init_db_creates_the_multi_market_tables(tmp_path):
    conn = sqlite_db(tmp_path)
    assert {"instruments", "market_prices", "fundamentals_pit_us"} <= _table_names(conn)


def test_init_db_is_idempotent(tmp_path):
    """init_db runs at the top of every pipeline run, so a second pass must be a
    no-op rather than an error or a truncation."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_instruments(
        conn, pd.DataFrame([dict(market="US", symbol="AAPL", currency="USD")]))
    storage.init_db(conn)
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 1


def test_rebuild_preserves_the_multi_market_tables(tmp_path):
    """A full rebuild drops the COTAHIST tables, which can be re-downloaded. It
    must NOT drop these: the forward-built archive and the listing intervals
    derived from it cannot be fetched again."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_instruments(
        conn, pd.DataFrame([dict(market="US", symbol="AAPL", currency="USD")]))
    storage.init_db(conn, rebuild=True)
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 1


# ── instruments ───────────────────────────────────────────────────────────────

def test_instruments_upsert_defaults_missing_columns_to_null(tmp_path):
    conn = sqlite_db(tmp_path)
    n = market_storage.upsert_instruments(
        conn, pd.DataFrame([dict(market="CRYPTO", symbol="BTCUSDT", currency="USDT")]))
    assert n == 1
    row = storage.read_sql("SELECT * FROM instruments", conn).iloc[0]
    assert row["venue"] is None and row["delisting_date"] is None
    assert row["is_worthless"] == 0


def test_instruments_upsert_updates_in_place(tmp_path):
    """A pair discovered alive and later found dead must update, not duplicate —
    the archive is re-scanned on every run."""
    conn = sqlite_db(tmp_path)
    base = dict(market="CRYPTO", symbol="SRMUSDT", currency="USDT")
    market_storage.upsert_instruments(conn, pd.DataFrame([base]))
    market_storage.upsert_instruments(
        conn, pd.DataFrame([{**base, "delisting_date": "2023-06-01",
                             "delist_reason": "archive ends"}]))
    df = storage.read_sql("SELECT * FROM instruments", conn)
    assert len(df) == 1
    assert df.iloc[0]["delisting_date"] == "2023-06-01"


def test_same_symbol_in_two_markets_does_not_collide(tmp_path):
    """The primary key is (market, symbol). A B3 ticker and a US ticker that
    happen to share a string are different instruments."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="B3", symbol="VALE3", currency="BRL"),
        dict(market="US", symbol="VALE3", currency="USD"),
    ]))
    df = storage.read_sql(
        "SELECT market, currency FROM instruments ORDER BY market", conn)
    assert list(df["market"]) == ["B3", "US"]
    assert list(df["currency"]) == ["BRL", "USD"]


# ── market_prices ─────────────────────────────────────────────────────────────

def _bar(**kw):
    row = dict(market="CRYPTO", symbol="SRMUSDT", date="2020-08-11", open=1.0,
               high=2.0, low=0.5, close=1.5, volume=1000.0, source="binance-archive")
    row.update(kw)
    return row


def test_market_prices_round_trip(tmp_path):
    conn = sqlite_db(tmp_path)
    assert market_storage.upsert_market_prices(conn, pd.DataFrame([_bar()])) == 1
    row = storage.read_sql("SELECT * FROM market_prices", conn).iloc[0]
    assert row["symbol"] == "SRMUSDT" and row["close"] == 1.5


def test_zero_volume_bar_survives(tmp_path):
    """upsert_prices drops zero-volume rows because a COTAHIST row with no volume
    did not trade. Here a zero-volume session is real — an illiquid name or a pair
    in its dying days prints one — and dropping it is exactly the silent
    survivorship hole this table exists to close."""
    conn = sqlite_db(tmp_path)
    assert market_storage.upsert_market_prices(
        conn, pd.DataFrame([_bar(volume=0.0)])) == 1


def test_invalid_ohlc_is_rejected(tmp_path):
    conn = sqlite_db(tmp_path)
    rows = pd.DataFrame([
        _bar(date="2020-08-11", close=9.9),          # close above high
        _bar(date="2020-08-12", high=0.1, low=2.0),  # high below low
        _bar(date="2020-08-13", close=-1.0),         # non-positive close
        _bar(date="2020-08-14"),                     # the only good one
    ])
    assert market_storage.upsert_market_prices(conn, rows) == 1
    assert storage.read_sql(
        "SELECT date FROM market_prices", conn).iloc[0]["date"] == "2020-08-14"


def test_market_prices_upsert_is_idempotent(tmp_path):
    conn = sqlite_db(tmp_path)
    bars = pd.DataFrame([_bar(date="2020-08-11"), _bar(date="2020-08-12")])
    market_storage.upsert_market_prices(conn, bars)
    market_storage.upsert_market_prices(conn, bars)
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM market_prices", conn).iloc[0]["c"] == 2


def test_brl_column_and_its_rate_are_stored_together(tmp_path):
    """A BRL figure with no recoverable rate is a number nobody can check, so the
    rate that produced it rides alongside it."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_market_prices(
        conn, pd.DataFrame([_bar(close=1.5, close_brl=8.16, fx_rate=5.44)]))
    row = storage.read_sql("SELECT * FROM market_prices", conn).iloc[0]
    assert row["close_brl"] == 8.16
    assert row["close"] * row["fx_rate"] == row["close_brl"]


def test_empty_frame_writes_nothing(tmp_path):
    conn = sqlite_db(tmp_path)
    assert market_storage.upsert_market_prices(conn, pd.DataFrame()) == 0
    assert market_storage.upsert_instruments(conn, pd.DataFrame()) == 0
    assert us_storage.upsert_fundamentals_pit_us(conn, pd.DataFrame()) == 0


# ── fundamentals_pit_us ───────────────────────────────────────────────────────

def _fact(**kw):
    row = dict(fact_id="acc-1:NetIncomeLoss:2023", cik="0000320193", ticker="AAPL",
               period_end="2023-09-30", filing_date="2023-11-03", accn="acc-1",
               form="10-K", fiscal_year=2023, fiscal_period="FY",
               net_income=96_995_000_000.0)
    row.update(kw)
    return row


def test_restatement_is_a_second_row_not_an_overwrite(tmp_path):
    """The point of the table: a revision filed later must not rewrite history. A
    backtest dated before the restatement still reads the number the market had.
    """
    conn = sqlite_db(tmp_path)
    us_storage.upsert_fundamentals_pit_us(conn, pd.DataFrame([
        _fact(),
        _fact(fact_id="acc-2:NetIncomeLoss:2023", accn="acc-2",
              filing_date="2024-11-01", net_income=96_000_000_000.0),
    ]))
    df = storage.read_sql(
        "SELECT filing_date, net_income FROM fundamentals_pit_us"
        " ORDER BY filing_date", conn)
    assert len(df) == 2
    assert df.iloc[0]["net_income"] != df.iloc[1]["net_income"]

    as_of = df[df["filing_date"] <= "2024-01-01"]
    assert len(as_of) == 1 and as_of.iloc[0]["net_income"] == 96_995_000_000.0


def test_rows_without_the_bitemporal_keys_are_dropped(tmp_path):
    """period_end and filing_date are what make a row point-in-time. A fact
    missing either cannot be placed in time and must not enter the table."""
    conn = sqlite_db(tmp_path)
    n = us_storage.upsert_fundamentals_pit_us(conn, pd.DataFrame([
        _fact(),
        _fact(fact_id="acc-3:x", filing_date=None),
        _fact(fact_id="acc-4:x", period_end=None),
    ]))
    assert n == 1


def test_us_fundamentals_upsert_is_idempotent(tmp_path):
    conn = sqlite_db(tmp_path)
    facts = pd.DataFrame([_fact()])
    us_storage.upsert_fundamentals_pit_us(conn, facts)
    us_storage.upsert_fundamentals_pit_us(conn, facts)
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM fundamentals_pit_us", conn).iloc[0]["c"] == 1


# ── Partial writes: four writers share `instruments` and none knows all of it ──

def test_a_narrow_write_does_not_erase_what_another_writer_knew(tmp_path):
    """The bug this pins cost the US plane its join key six days out of seven.

    `upsert_instruments` used to fill every absent column with NULL and overwrite
    unconditionally. The consolidated symbol directory runs nightly and knows
    name/venue/kind; the SEC ingest runs weekly and knows the CIK. So every
    weeknight at 23:00 the directory blanked the CIK the Sunday ingest had
    written — and `instruments.cik` is the only thing joining US fundamentals to
    US prices.
    """
    conn = sqlite_db(tmp_path)
    # Sunday: the SEC ingest knows the CIK and nothing else.
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="US", symbol="AAPL", currency="USD", kind="EQUITY",
             cik="0000320193")]))
    # Monday night: the directory knows the name and the venue, not the CIK.
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="US", symbol="AAPL", currency="USD", name="Apple Inc.",
             venue="NASDAQ", first_seen="2026-01-05", last_seen="2026-01-05")]))

    row = storage.read_sql(
        "SELECT * FROM instruments WHERE market='US' AND symbol='AAPL'", conn).iloc[0]
    assert row["cik"] == "0000320193"      # survived the nightly snapshot
    assert row["name"] == "Apple Inc."     # and the nightly snapshot still landed
    assert row["venue"] == "NASDAQ"
    assert str(row["first_seen"]) == "2026-01-05"


def test_a_new_row_still_lands_with_every_column_the_caller_did_not_supply(tmp_path):
    """Narrowing applies to the UPDATE, not the INSERT: a first sighting has no
    prior row to protect, and the absent columns are genuinely unknown."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="US", symbol="NEWCO", currency="USD", cik="0001234567")]))
    row = storage.read_sql(
        "SELECT * FROM instruments WHERE symbol='NEWCO'", conn).iloc[0]
    assert row["cik"] == "0001234567"
    assert row["name"] is None and row["venue"] is None
    # DEFAULT 0 columns are filled explicitly — an explicit NULL bypasses a
    # column default, and `universe.delisting_returns` reads is_worthless.
    assert int(row["is_worthless"]) == 0
    assert int(row["is_test_issue"]) == 0


def test_a_write_without_currency_is_refused_by_name(tmp_path):
    """`currency` is NOT NULL, so a narrow write that omits it cannot create a
    row. Failing here names the column; failing in the driver names it three
    frames down, inside SQL the caller never wrote."""
    conn = sqlite_db(tmp_path)
    market_storage.upsert_instruments(conn, pd.DataFrame([
        dict(market="US", symbol="AAPL", currency="USD", name="Apple Inc.")]))
    with pytest.raises(ValueError, match="currency"):
        market_storage.upsert_instruments(conn, pd.DataFrame([
            dict(market="US", symbol="AAPL", cik="0000320193")]))
