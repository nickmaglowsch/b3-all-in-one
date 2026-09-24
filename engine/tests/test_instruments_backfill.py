"""Tests for the B3 security master backfill.

The property that matters most here is what the backfill refuses to do: it must
not invent a delisting. A B3 ticker that simply goes quiet is not delisted, and
writing a delisting date for it would truncate a live name's history in every
backtest.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from b3_pipeline import instruments_backfill, storage
from tests.conftest import sqlite_db


def _price(conn, ticker, date, isin="UNKNOWN"):
    conn.execute(
        "INSERT INTO prices (ticker, isin_code, date, open, high, low, close,"
        " adj_close, volume) VALUES (?,?,?,?,?,?,?,?,?)",
        (ticker, isin, date, 10.0, 10.0, 10.0, 10.0, 10.0, 100.0))
    conn.commit()


def _cvm(conn, cnpj, root, delisting=None):
    conn.execute(
        "INSERT INTO cvm_companies (cnpj, ticker, delisting_date) VALUES (?,?,?)",
        (cnpj, root, delisting))
    conn.commit()


def _fca(conn, cnpj, ticker, end=None):
    conn.execute(
        "INSERT INTO company_tickers_pit (cnpj, ticker, start_date, end_date)"
        " VALUES (?,?,?,?)", (cnpj, ticker, "2000-01-01", end))
    conn.commit()


def _row(conn, symbol):
    df = instruments_backfill.build(conn)
    return df[df["symbol"] == symbol].iloc[0]


def _stored(conn, symbol):
    """The row as the plane actually holds it — NaN is normalised to SQL NULL on
    the way in, so a frame-level check cannot tell a sentinel from a null."""
    from b3_pipeline import market_storage
    market_storage.upsert_instruments(conn, instruments_backfill.build(conn))
    df = storage.read_sql(
        "SELECT * FROM instruments WHERE symbol = ?", conn, params=[symbol])
    return df.iloc[0]


def test_observed_bounds_come_from_the_price_plane(tmp_path):
    conn = sqlite_db(tmp_path)
    for d in ("2010-01-04", "2015-06-30", "2020-12-30"):
        _price(conn, "PETR4", d)
    row = _row(conn, "PETR4")
    assert str(row["first_seen"].date()) == "2010-01-04"
    assert str(row["last_seen"].date()) == "2020-12-30"


def test_a_ticker_nobody_delisted_stays_open_ended(tmp_path):
    """The refusal that matters. A B3 name can go quiet and come back, and a
    year can be missing from the plane entirely — silence is not a delisting."""
    conn = sqlite_db(tmp_path)
    _price(conn, "MGLU3", "2010-01-04")
    assert pd.isna(_row(conn, "MGLU3")["delisting_date"])
    assert _stored(conn, "MGLU3")["delist_reason"] is None


def test_a_cvm_delisting_is_recorded(tmp_path):
    conn = sqlite_db(tmp_path)
    _price(conn, "VALE5", "2010-01-04")
    _price(conn, "VALE5", "2017-06-30")
    _cvm(conn, "111", "VALE", delisting="2017-06-30")
    assert str(_row(conn, "VALE5")["delisting_date"].date()) == "2017-06-30"


def test_a_claimed_delisting_is_clamped_to_the_last_traded_session(tmp_path):
    """CVM's date is a registration event and often lands months after the last
    trade. Taken literally it would let the forward fill carry a flat price
    across that gap — the smaller version of the bug this all removes."""
    conn = sqlite_db(tmp_path)
    _price(conn, "OGXP3", "2010-01-04")
    _price(conn, "OGXP3", "2013-05-31")
    _cvm(conn, "222", "OGXP", delisting="2014-12-31")
    assert str(_row(conn, "OGXP3")["delisting_date"].date()) == "2013-05-31"


def test_the_earlier_of_the_two_sources_wins(tmp_path):
    conn = sqlite_db(tmp_path)
    _price(conn, "ABCD3", "2010-01-04")
    _price(conn, "ABCD3", "2018-01-04")
    _cvm(conn, "333", "ABCD", delisting="2016-01-01")
    _fca(conn, "333", "ABCD3", end="2015-01-01")
    assert str(_row(conn, "ABCD3")["delisting_date"].date()) == "2015-01-01"


def test_nothing_is_ever_marked_worthless(tmp_path):
    """CVM records that a company deregistered, not what shareholders recovered.
    Guessing zero would fabricate a total loss on every acquisition."""
    conn = sqlite_db(tmp_path)
    _price(conn, "VALE5", "2010-01-04")
    _cvm(conn, "111", "VALE", delisting="2010-01-04")
    assert _row(conn, "VALE5")["is_worthless"] == 0


def test_the_unknown_isin_sentinel_does_not_leak(tmp_path):
    conn = sqlite_db(tmp_path)
    _price(conn, "MGLU3", "2010-01-04", isin="UNKNOWN")
    _price(conn, "PETR4", "2010-01-04", isin="BRPETRACNPR6")
    assert _stored(conn, "MGLU3")["isin_code"] is None
    assert _stored(conn, "PETR4")["isin_code"] == "BRPETRACNPR6"


def test_backfill_is_idempotent(tmp_path):
    conn = sqlite_db(tmp_path)
    _price(conn, "PETR4", "2010-01-04")
    from b3_pipeline import market_storage
    for _ in range(2):
        market_storage.upsert_instruments(conn, instruments_backfill.build(conn))
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 1


def test_an_empty_plane_writes_nothing(tmp_path):
    conn = sqlite_db(tmp_path)
    assert instruments_backfill.build(conn).empty


def test_the_backfill_activates_the_survivorship_stop(tmp_path):
    """End to end: with the master filled, a delisted name's price stops. This is
    the moment the behaviour actually changes for B3 — deliberately at backfill
    time, visibly, rather than at deploy time."""
    from b3_pipeline import market_storage
    from backtests.core.data import load_b3_data

    db_path = str(tmp_path / "b3.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    for d in ("2010-01-04", "2010-01-05"):
        _price(conn, "DEAD3", d)
        _price(conn, "LIVE3", d)
    _price(conn, "LIVE3", "2010-01-06")
    _cvm(conn, "444", "DEAD", delisting="2010-01-05")

    before, _, _ = load_b3_data(db_path, "2010-01-01", "2010-01-31")
    assert before["DEAD3"].iloc[-1] == 10.0        # flat carry, the old behaviour

    market_storage.upsert_instruments(conn, instruments_backfill.build(conn))
    conn.close()

    after, _, _ = load_b3_data(db_path, "2010-01-01", "2010-01-31")
    assert pd.isna(after["DEAD3"].iloc[-1])
    assert after["LIVE3"].iloc[-1] == 10.0
