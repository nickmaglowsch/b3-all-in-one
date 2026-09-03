"""The Postgres read path streams, and streaming changes nothing but the memory.

storage.read_sql used to hand a bare psycopg connection to pd.read_sql_query.
Pandas has no fast path for that, so it fetchall()s the whole result into Python
tuples before building the frame — 3.1 GB peak to produce a 389 MB frame on the
one query that matters (every B3 bar in a backtest window). sql_stream fetches
through a server-side cursor instead, in chunks.

The risk of chunking is not that a row goes missing; it is that a column types
differently because a chunk boundary fell somewhere unlucky. That is what most
of this file is about. The rest needs a live Postgres and skips without one, the
same way tests/test_postgres_storage.py does.
"""
from __future__ import annotations

import datetime
import os
import warnings

import pandas as pd
import pytest

from b3_pipeline import sql_stream, storage

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("ENGINE_TEST_DSN") or os.environ.get("ENGINE_DATABASE_URL") or ""


def _live_dsn() -> str:
    if not DSN.startswith(("postgresql://", "postgres://")):
        pytest.skip("no Postgres DSN (set ENGINE_TEST_DSN)")
    try:
        psycopg.connect(DSN).close()
    except Exception as exc:                                        # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {exc}")
    return DSN


@pytest.fixture
def conn():
    c = psycopg.connect(_live_dsn())
    with c.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS sql_stream_probe")
        cur.execute("CREATE TEMP TABLE sql_stream_probe "
                    "(i INTEGER, d DATE, t TEXT, f REAL, sparse REAL)")
    c.commit()
    yield c
    c.close()


def _fill(conn, rows: int, *, sparse_from: int | None = None) -> None:
    """`rows` rows; `sparse` is NULL from `sparse_from` on (else always set)."""
    with conn.cursor() as cur, cur.copy(
        "COPY sql_stream_probe (i, d, t, f, sparse) FROM STDIN"
    ) as cp:
        for i in range(rows):
            null = sparse_from is not None and i >= sparse_from
            cp.write_row((i, datetime.date(2020, 1, 1) + datetime.timedelta(days=i % 28),
                          f"TICK{i % 7}", float(i) / 4, None if null else float(i)))
    conn.commit()


def _pandas_read(conn, sql: str, params=None) -> pd.DataFrame:
    """What read_sql did before this module existed."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.read_sql_query(storage._xlate(conn, sql, params), conn, params=params)


ORDERED = "SELECT i, d, t, f, sparse FROM sql_stream_probe ORDER BY i"


def test_streamed_frame_is_identical_to_the_pandas_one(conn):
    _fill(conn, 500)
    pd.testing.assert_frame_equal(
        storage.read_sql(ORDERED, conn), _pandas_read(conn, ORDERED))


def test_it_really_did_take_more_than_one_chunk(conn, monkeypatch):
    """Guard on the guard: with CHUNK_ROWS above the row count every test below
    would exercise the single-chunk path and prove nothing about chunking."""
    monkeypatch.setattr(sql_stream, "CHUNK_ROWS", 100)
    _fill(conn, 500)
    calls = []
    real = pd.DataFrame.from_records
    monkeypatch.setattr(
        pd.DataFrame, "from_records",
        staticmethod(lambda *a, **k: (calls.append(1), real(*a, **k))[1]))
    storage.read_sql(ORDERED, conn)
    assert len(calls) == 5, f"expected 5 chunks of 100, got {len(calls)}"


def test_chunk_boundaries_do_not_change_the_frame(conn, monkeypatch):
    """The same rows, read one chunk or many, must compare equal — values and
    dtypes both."""
    _fill(conn, 500)
    whole = storage.read_sql(ORDERED, conn)
    for chunk in (1, 7, 100, 499, 500, 501):
        monkeypatch.setattr(sql_stream, "CHUNK_ROWS", chunk)
        pd.testing.assert_frame_equal(storage.read_sql(ORDERED, conn), whole)


def test_a_column_that_is_all_null_in_one_chunk_still_types_as_float(conn, monkeypatch):
    """The failure chunking could actually cause. `sparse` has values in the
    first 200 rows and NULL after, so with 100-row chunks three whole chunks
    type it as object; concat spreads that to the column and every downstream
    `.rolling()` on it breaks. infer_objects is what puts it back."""
    _fill(conn, 500, sparse_from=200)
    monkeypatch.setattr(sql_stream, "CHUNK_ROWS", 100)
    streamed = storage.read_sql(ORDERED, conn)
    assert streamed["sparse"].dtype == _pandas_read(conn, ORDERED)["sparse"].dtype
    assert streamed["sparse"].dtype == "float64"
    pd.testing.assert_frame_equal(streamed, _pandas_read(conn, ORDERED))


def test_a_date_column_is_left_as_objects_not_silently_parsed(conn):
    """infer_objects must not go further than undoing the chunk split.
    load_market_hlc_data calls pd.to_datetime on this column itself, and other
    readers treat it as dates; converting it here would be a change of contract
    smuggled in under a memory fix."""
    _fill(conn, 300)
    col = storage.read_sql(ORDERED, conn)["d"]
    assert col.dtype == object
    assert isinstance(col.iloc[0], datetime.date)


def test_an_empty_result_keeps_its_columns(conn):
    """Zero rows means zero chunks — the column names have to come off the
    cursor description, not off a frame that was never built. Callers index by
    name on the way to an empty frame (universe.load_listing_intervals)."""
    _fill(conn, 10)
    empty = storage.read_sql(ORDERED + " OFFSET 1000", conn)
    assert len(empty) == 0
    assert list(empty.columns) == ["i", "d", "t", "f", "sparse"]


@pytest.mark.parametrize("sql, params", [
    ("SELECT i FROM sql_stream_probe WHERE i >= :lo ORDER BY i", {"lo": 5}),
    ("SELECT i FROM sql_stream_probe WHERE i >= ? ORDER BY i", [5]),
])
def test_both_placeholder_styles_still_translate(conn, sql, params):
    _fill(conn, 20)
    assert storage.read_sql(sql, conn, params=params)["i"].tolist() == list(range(5, 20))


def test_a_literal_percent_survives_with_and_without_params(conn):
    """The regression tests/test_sql_placeholders.py pins at the _xlate level,
    now driven end to end: psycopg only parses placeholders when it is given
    parameters, so the two cases take different paths through the escaping."""
    _fill(conn, 20)
    like = "SELECT DISTINCT t FROM sql_stream_probe WHERE t LIKE '%3'"
    assert storage.read_sql(like, conn)["t"].tolist() == ["TICK3"]
    assert storage.read_sql(like + " AND i >= :lo", conn,
                            params={"lo": 0})["t"].tolist() == ["TICK3"]


def test_two_streams_can_be_open_at_once(conn):
    """Server-side cursors are named. A constant name would raise DuplicateCursor
    here rather than at some unlucky call site later."""
    _fill(conn, 300)
    with conn.cursor(name="b3_stream_probe_outer") as outer:
        outer.execute(ORDERED)
        outer.fetchone()
        assert len(storage.read_sql(ORDERED, conn)) == 300


def test_kwargs_fall_back_to_pandas(conn):
    """sql_stream implements the plain read. A caller that wants pandas' own
    post-processing (parse_dates, index_col) must still get it."""
    _fill(conn, 50)
    df = storage.read_sql(ORDERED, conn, parse_dates=["d"])
    assert pd.api.types.is_datetime64_any_dtype(df["d"])


def test_sqlite_does_not_go_through_the_stream(monkeypatch, tmp_path):
    """The SQLite path is unchanged, and there is no server-side cursor to take.
    Blowing up if it is reached is the clearest way to say so."""
    monkeypatch.setattr(sql_stream, "read_dataframe",
                        lambda *a, **k: pytest.fail("sqlite reached sql_stream"))
    conn = storage.get_connection(tmp_path / "probe.sqlite")
    try:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        assert storage.read_sql("SELECT a FROM t", conn)["a"].tolist() == [1]
    finally:
        conn.close()
