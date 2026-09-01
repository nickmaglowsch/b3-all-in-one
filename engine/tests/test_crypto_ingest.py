"""Tests for the crypto ingest: quote resolution, delisting inference, BRL.

Offline — `binance_archive` is stubbed. The archive reader's own contract is
covered in test_binance_archive.py.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd

from b3_pipeline import storage
from crypto_pipeline import crypto_main

TODAY = datetime.date(2026, 9, 1)


def _bars(symbol, dates, close=1.5):
    return pd.DataFrame({
        "market": "CRYPTO", "symbol": symbol, "date": [
            datetime.date.fromisoformat(d) for d in dates],
        "open": close, "high": close * 2, "low": close / 2, "close": close,
        "volume": 1000.0, "adj_close": close, "split_adj_close": close,
        "source": "binance-archive",
    })


# ── quote resolution ──────────────────────────────────────────────────────────

def test_longest_quote_suffix_wins():
    """Several Binance quote assets end in the string of another one — FDUSD,
    BUSD and TUSD all end in USD. First-match-wins would file every BTCFDUSD bar
    under a USD pair that does not exist, so the suffix search runs longest
    first."""
    quotes = ("FDUSD", "USDT", "USD", "BRL")
    assert crypto_main._quote_of("BTCFDUSD", quotes) == "FDUSD"
    assert crypto_main._quote_of("BTCUSDT", quotes) == "USDT"
    assert crypto_main._quote_of("BTCUSD", quotes) == "USD"
    assert crypto_main._quote_of("BTCBRL", quotes) == "BRL"


def test_a_pair_in_no_requested_quote_is_skipped():
    assert crypto_main._quote_of("BTCETH", ("USDT", "BRL")) == ""


def test_selection_keeps_dead_pairs(monkeypatch):
    """No liveness filter, by design: enumerating from the bucket rather than
    from exchangeInfo is the entire survivorship mechanism."""
    monkeypatch.setattr(crypto_main.archive, "list_symbols",
                        lambda *a, **k: ["SRMUSDT", "BTCUSDT", "BTCETH"])
    assert crypto_main.select_symbols(("USDT",)) == ["SRMUSDT", "BTCUSDT"]


# ── delisting inference ───────────────────────────────────────────────────────

def test_a_pair_whose_archive_stopped_is_marked_delisted():
    row = crypto_main._instrument_row(
        "SRMUSDT", "USDT", _bars("SRMUSDT", ["2020-08-11", "2022-11-30"]), TODAY)
    assert row["listing_date"] == datetime.date(2020, 8, 11)
    assert row["delisting_date"] == datetime.date(2022, 11, 30)


def test_a_live_pair_has_no_delisting_date():
    row = crypto_main._instrument_row(
        "BTCUSDT", "USDT", _bars("BTCUSDT", ["2017-08-17", "2026-08-31"]), TODAY)
    assert row["delisting_date"] is None
    assert row["delist_reason"] is None


def test_the_monthly_publication_lag_does_not_kill_a_live_pair():
    """The monthly file for the current month only lands once the month ends, so
    a living pair is routinely weeks behind. Declaring it dead on the 1st would
    delist the entire universe every month."""
    lagging = _bars("BTCUSDT", ["2017-08-17", "2026-07-31"])
    assert crypto_main._instrument_row(
        "BTCUSDT", "USDT", lagging, TODAY)["delisting_date"] is None


def test_delisting_is_scoped_to_the_venue():
    """This plane is Binance spot. A pair that left Binance may still trade
    elsewhere, and the reason string has to say so rather than claim the token
    died."""
    row = crypto_main._instrument_row(
        "SRMUSDT", "USDT", _bars("SRMUSDT", ["2020-08-11", "2022-11-30"]), TODAY)
    assert "Binance" in row["delist_reason"]


def test_a_delisted_pair_is_not_assumed_worthless():
    """Leaving an exchange is not going to zero. The simulator liquidates at the
    last mark unless something positively says the token was wiped out."""
    row = crypto_main._instrument_row(
        "SRMUSDT", "USDT", _bars("SRMUSDT", ["2020-08-11", "2022-11-30"]), TODAY)
    assert row["is_worthless"] == 0


# ── BRL materialisation ───────────────────────────────────────────────────────

PTAX = pd.Series([5.0], index=pd.to_datetime(["2020-01-01"]))


def test_a_brl_quoted_pair_needs_no_conversion():
    out = crypto_main._with_brl(_bars("BTCBRL", ["2020-08-11"], close=300000.0),
                                "BRL", PTAX)
    assert out.iloc[0]["fx_rate"] == 1.0
    assert out.iloc[0]["close_brl"] == 300000.0


def test_a_dollar_quoted_pair_converts_through_ptax():
    out = crypto_main._with_brl(_bars("BTCUSDT", ["2020-08-11"], close=10.0),
                                "USDT", PTAX)
    assert out.iloc[0]["close_brl"] == 50.0


def test_a_quote_asset_with_no_rate_gets_no_brl_columns():
    """An ETH-quoted pair has no PTAX path. NULL BRL is right; guessing is not.
    The native prices are still stored and still tradable in a native backtest."""
    out = crypto_main._with_brl(_bars("BNBETH", ["2020-08-11"]), "ETH", PTAX)
    assert "close_brl" not in out.columns or out["close_brl"].isna().all()


# ── end to end, against the real writers ──────────────────────────────────────

def test_ingest_writes_instrument_and_prices_together(tmp_path, monkeypatch):
    conn = sqlite3.connect(str(tmp_path / "c.sqlite"))
    storage.init_db(conn)
    monkeypatch.setattr(crypto_main.archive, "fetch_month",
                        lambda s, m: _bars(s, ["2020-08-11", "2020-08-12"]))

    n = crypto_main.ingest_symbol(conn, "SRMUSDT", "USDT", PTAX, TODAY,
                                  months=["2020-08"])
    assert n == 2
    instr = storage.read_sql("SELECT * FROM instruments", conn)
    assert len(instr) == 1 and instr.iloc[0]["symbol"] == "SRMUSDT"
    assert instr.iloc[0]["delisting_date"] == "2020-08-12"
    px = storage.read_sql("SELECT close, close_brl FROM market_prices", conn)
    assert len(px) == 2 and px.iloc[0]["close_brl"] == 7.5


def test_a_pair_with_no_files_writes_nothing(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "c.sqlite"))
    storage.init_db(conn)
    assert crypto_main.ingest_symbol(conn, "GONE", "USDT", PTAX, TODAY, months=[]) == 0
    assert storage.read_sql("SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 0


def test_ingest_is_idempotent(tmp_path, monkeypatch):
    """The archive is re-scanned on every run, so a second pass must update in
    place rather than duplicate a pair or its bars."""
    conn = sqlite3.connect(str(tmp_path / "c.sqlite"))
    storage.init_db(conn)
    monkeypatch.setattr(crypto_main.archive, "fetch_month",
                        lambda s, m: _bars(s, ["2020-08-11"]))
    for _ in range(2):
        crypto_main.ingest_symbol(conn, "SRMUSDT", "USDT", PTAX, TODAY,
                                  months=["2020-08"])
    assert storage.read_sql("SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 1
    assert storage.read_sql("SELECT COUNT(*) c FROM market_prices", conn).iloc[0]["c"] == 1
