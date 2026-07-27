"""upsert_prices / update_adjusted_columns write the whole price table row by row.

Both went through cursor.executemany, which is one statement per row against the
largest table in the database — 4.3s and 7.9s respectively over 302k rows, every
pipeline run, growing linearly with history.

Postgres can stage the batch with COPY and finish in one set-based statement.
That is only safe if it keeps the exact semantics executemany had, so these pin
them for both backends: what gets stored, what a conflict does, and which row
wins when the same key appears twice in one call.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from b3_pipeline import storage

PRICE_COLS = ["ticker", "isin_code", "date", "open", "high", "low", "close",
              "volume", "quotation_factor"]


def _rows(*specs) -> pd.DataFrame:
    return pd.DataFrame(
        [dict(zip(PRICE_COLS, s)) for s in specs],
        columns=PRICE_COLS,
    )


def _row(ticker="PETR4", date="2024-01-02", close=30.0, volume=1000.0,
         isin="BRPETRACNPR6", qf=1):
    return (ticker, isin, date, close, close, close, close, volume, qf)


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "px.sqlite"))
    storage.init_db(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def pg_conn():
    """A throwaway schema on the local plane, so the real tables are untouched."""
    if not storage.using_postgres():
        pytest.skip("ENGINE_DATABASE_URL is not a postgresql:// DSN")
    conn = storage.get_connection(None)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS bulk_write_test CASCADE")
    cur.execute("CREATE SCHEMA bulk_write_test")
    cur.execute("SET search_path TO bulk_write_test")
    storage.init_db(conn)
    conn.commit()
    yield conn
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS bulk_write_test CASCADE")
    conn.commit()
    conn.close()


def _read(conn, cols="ticker, date, close, volume, quotation_factor"):
    cur = conn.cursor()
    cur.execute(f"SELECT {cols} FROM prices ORDER BY ticker, date")
    return [tuple(str(v) if hasattr(v, "isoformat") else v for v in r)
            for r in cur.fetchall()]


BACKENDS = ["sqlite_conn", "pg_conn"]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_upsert_stores_every_row(conn_fixture, request):
    conn = request.getfixturevalue(conn_fixture)
    storage.upsert_prices(conn, _rows(
        _row("PETR4", "2024-01-02", 30.0),
        _row("VALE3", "2024-01-02", 60.0),
        _row("PETR4", "2024-01-03", 31.0),
    ))
    assert _read(conn) == [
        ("PETR4", "2024-01-02", 30.0, 1000.0, 1),
        ("PETR4", "2024-01-03", 31.0, 1000.0, 1),
        ("VALE3", "2024-01-02", 60.0, 1000.0, 1),
    ]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_upsert_overwrites_an_existing_key(conn_fixture, request):
    conn = request.getfixturevalue(conn_fixture)
    storage.upsert_prices(conn, _rows(_row("PETR4", "2024-01-02", 30.0)))
    storage.upsert_prices(conn, _rows(_row("PETR4", "2024-01-02", 33.0, volume=2000.0)))
    assert _read(conn) == [("PETR4", "2024-01-02", 33.0, 2000.0, 1)]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_duplicate_key_within_one_batch_keeps_the_last_row(conn_fixture, request):
    """executemany applied rows in order, so the last one won. A COPY + one
    INSERT ... ON CONFLICT would otherwise raise "cannot affect row a second
    time", or silently keep whichever row the planner reached first."""
    conn = request.getfixturevalue(conn_fixture)
    storage.upsert_prices(conn, _rows(
        _row("PETR4", "2024-01-02", 30.0, volume=1000.0),
        _row("PETR4", "2024-01-02", 31.0, volume=2000.0),
        _row("PETR4", "2024-01-02", 32.0, volume=3000.0),
    ))
    assert _read(conn) == [("PETR4", "2024-01-02", 32.0, 3000.0, 1)]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_upsert_still_drops_bad_rows(conn_fixture, request):
    """The validation in front of the write is not part of the batching change:
    zero volume, non-positive close and inconsistent OHLC must still be dropped."""
    conn = request.getfixturevalue(conn_fixture)
    df = _rows(
        _row("GOOD3", "2024-01-02", 10.0),
        ("ZEROVOL3", "X", "2024-01-02", 10.0, 10.0, 10.0, 10.0, 0.0, 1),
        ("NEGCLOSE3", "X", "2024-01-02", 10.0, 10.0, 10.0, -1.0, 100.0, 1),
    )
    # close above high -> inconsistent OHLC
    df.loc[len(df)] = ("BADOHLC3", "X", "2024-01-02", 10.0, 10.0, 9.0, 20.0, 100.0, 1)
    storage.upsert_prices(conn, df)
    assert [r[0] for r in _read(conn)] == ["GOOD3"]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_upsert_defaults_missing_isin_and_quotation_factor(conn_fixture, request):
    conn = request.getfixturevalue(conn_fixture)
    df = _rows(_row("PETR4", "2024-01-02", 30.0)).drop(
        columns=["isin_code", "quotation_factor"])
    storage.upsert_prices(conn, df)
    assert _read(conn, "ticker, isin_code, quotation_factor") == [
        ("PETR4", "UNKNOWN", 1)]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_update_adjusted_columns_writes_only_the_adjusted_columns(conn_fixture, request):
    conn = request.getfixturevalue(conn_fixture)
    storage.upsert_prices(conn, _rows(
        _row("PETR4", "2024-01-02", 30.0),
        _row("VALE3", "2024-01-02", 60.0),
    ))
    adj = pd.DataFrame([{
        "ticker": "PETR4", "isin_code": "BRPETRACNPR6", "date": "2024-01-02",
        "split_adj_open": 15.0, "split_adj_high": 15.0, "split_adj_low": 15.0,
        "split_adj_close": 15.0, "adj_close": 14.5, "volume": 999.0,
    }])
    storage.update_adjusted_columns(conn, adj)

    got = _read(conn, "ticker, close, adj_close, split_adj_close, volume")
    assert got == [
        ("PETR4", 30.0, 14.5, 15.0, 999.0),   # raw close untouched
        ("VALE3", 60.0, None, None, 1000.0),  # row absent from the frame untouched
    ]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_update_adjusted_columns_accepts_nulls(conn_fixture, request):
    conn = request.getfixturevalue(conn_fixture)
    storage.upsert_prices(conn, _rows(_row("PETR4", "2024-01-02", 30.0)))
    adj = pd.DataFrame([{
        "ticker": "PETR4", "isin_code": "BRPETRACNPR6", "date": "2024-01-02",
        "adj_close": None, "split_adj_close": 12.0,
    }])
    storage.update_adjusted_columns(conn, adj)
    assert _read(conn, "ticker, adj_close, split_adj_close") == [("PETR4", None, 12.0)]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_update_adjusted_columns_matches_on_isin_too(conn_fixture, request):
    """The UPDATE keys on (ticker, isin_code, date). A frame carrying a different
    isin_code for the same ticker/date must not match."""
    conn = request.getfixturevalue(conn_fixture)
    storage.upsert_prices(conn, _rows(_row("PETR4", "2024-01-02", 30.0)))
    adj = pd.DataFrame([{
        "ticker": "PETR4", "isin_code": "WRONGISIN0000", "date": "2024-01-02",
        "adj_close": 99.0,
    }])
    storage.update_adjusted_columns(conn, adj)
    assert _read(conn, "ticker, adj_close") == [("PETR4", None)]


@pytest.mark.parametrize("conn_fixture", BACKENDS)
def test_empty_frames_are_no_ops(conn_fixture, request):
    conn = request.getfixturevalue(conn_fixture)
    assert storage.upsert_prices(conn, pd.DataFrame()) == 0
    assert storage.update_adjusted_columns(conn, pd.DataFrame()) == 0


def test_both_backends_store_the_same_thing(sqlite_conn, request):
    """Parity: the COPY path and the executemany path must be interchangeable.

    Volumes here are whole numbers on purpose. `prices.volume` is declared
    INTEGER, which _pg_ddl widens to BIGINT, so Postgres rounds a fractional
    volume while SQLite's dynamic typing keeps it — a pre-existing divergence
    that has nothing to do with how the rows are written, and one this test
    would otherwise fail on for the wrong reason.
    """
    if not storage.using_postgres():
        pytest.skip("ENGINE_DATABASE_URL is not a postgresql:// DSN")
    pg = request.getfixturevalue("pg_conn")
    df = _rows(
        _row("PETR4", "2024-01-02", 30.0),
        _row("VALE3", "2024-01-03", 60.5, volume=1234.0),
        _row("HGLG11", "2024-01-04", 150.25, qf=1000),
    )
    for conn in (sqlite_conn, pg):
        storage.upsert_prices(conn, df)
    assert _read(sqlite_conn) == _read(pg)
