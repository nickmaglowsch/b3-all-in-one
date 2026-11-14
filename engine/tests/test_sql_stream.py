"""The Postgres read path streams, and streaming changes nothing but the memory.

storage.read_sql used to hand a bare psycopg connection to pd.read_sql_query.
Pandas has no fast path for that, so it fetchall()s the whole result into Python
tuples before building the frame — 3.1 GB peak to produce a 389 MB frame on the
one query that matters (every B3 bar in a backtest window). sql_stream fetches
through a server-side cursor instead, in chunks.

SQLite has no server-side cursor to take, so it batches through pandas' own
`chunksize` and lands in the same assembly — same reason, same guarantees.

The risk of chunking is not that a row goes missing; it is that a column types
differently because a chunk boundary fell somewhere unlucky. That is what most
of this file is about. The tests that need a live Postgres skip without one, the
same way tests/test_postgres_storage.py does; the assembly and the SQLite path
are exercised either way.
"""
from __future__ import annotations

import datetime
import os
import warnings

import pandas as pd
import pytest

from b3_pipeline import sql_copy, sql_stream, storage

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


# ── the chunk-assembly half, which needs no server ───────────────────────────
#
# read_dataframe's two memory behaviours are pure functions of the chunk frames,
# so they are tested against synthetic chunks: everything below the fixture needs
# a live Postgres and skips without one, which would otherwise mean these never
# run in CI at all.

def _chunk(lo: int, hi: int, *, sparse: bool = True) -> pd.DataFrame:
    """A chunk shaped like the probe table's, built the way read_dataframe does."""
    return pd.DataFrame.from_records(
        [(i, datetime.date(2020, 1, 1) + datetime.timedelta(days=i % 28),
          f"TICK{i % 7}", i / 4, (float(i) if sparse else None))
         for i in range(lo, hi)],
        columns=["i", "d", "t", "f", "sparse"], coerce_float=True)


COLUMNS = ["i", "d", "t", "f", "sparse"]


def test_assembling_column_by_column_equals_concatenating_the_chunks():
    """The memory shape changes; the frame does not. Column-at-a-time exists so
    the largest read in the process never exists twice."""
    chunks = [_chunk(0, 200), _chunk(200, 350), _chunk(350, 500)]
    expected = pd.concat([c.copy() for c in chunks],
                         ignore_index=True).infer_objects()
    pd.testing.assert_frame_equal(sql_stream._assemble(chunks, COLUMNS), expected)


def test_a_column_all_null_in_one_chunk_still_types_as_float_after_assembly():
    """The failure chunking can actually cause, at the level it happens: an
    all-NULL chunk types object, and without infer_objects that spreads to the
    whole column and every downstream .rolling() on it breaks."""
    chunks = [_chunk(0, 100), _chunk(100, 200, sparse=False), _chunk(200, 300)]
    assert sql_stream._assemble(chunks, COLUMNS)["sparse"].dtype == "float64"


def test_assembly_of_one_chunk_is_that_chunk():
    only = _chunk(0, 50)
    assert sql_stream._assemble([only], COLUMNS) is only


def test_assembly_of_nothing_keeps_the_columns():
    """Zero rows means zero chunks — the names come off the cursor description."""
    empty = sql_stream._assemble([], COLUMNS)
    assert len(empty) == 0 and list(empty.columns) == COLUMNS


def test_pooling_collapses_repeated_objects_without_changing_a_value():
    """The driver mints a new object per row. On the US plane that is ~30M str
    instances of a few thousand symbols and ~30M date instances of a few thousand
    days — gigabytes of duplicates, and the largest thing in the process while
    the read runs."""
    chunks = [_chunk(0, 200), _chunk(200, 400)]
    expected = pd.concat([c.copy() for c in chunks],
                         ignore_index=True).infer_objects()

    pools: dict = {}
    for chunk in chunks:
        sql_stream._pool_objects(chunk, pools)
    got = sql_stream._assemble(chunks, COLUMNS)

    pd.testing.assert_frame_equal(got, expected)
    assert len({id(v) for v in got["d"]}) == 28, "one object per distinct date"


def test_pooling_is_shared_across_chunks_not_restarted_per_chunk():
    """Per-chunk pooling would still leave one object per distinct value *per
    chunk* — 300 chunks of a big read is back to millions of them."""
    a, b = _chunk(0, 100), _chunk(100, 200)
    pools: dict = {}
    sql_stream._pool_objects(a, pools)
    sql_stream._pool_objects(b, pools)
    assert {id(v) for v in b["d"]} <= {id(v) for v in a["d"]}


def test_pooling_gives_up_on_a_column_that_is_distinct_per_row(monkeypatch):
    """On a column with no repeats the pool would grow to the row count and cost
    more than it saves. It stops, and the values are still exactly the values."""
    monkeypatch.setattr(sql_stream, "_POOL_MAX", 10)
    chunks = [_chunk(0, 50), _chunk(50, 100)]
    expected = pd.concat([c.copy() for c in chunks],
                         ignore_index=True).infer_objects()
    pools: dict = {}
    for chunk in chunks:
        chunk["uniq"] = [object() for _ in range(len(chunk))]
        sql_stream._pool_objects(chunk, pools)
        del chunk["uniq"]
    assert pools["uniq"] is None
    pd.testing.assert_frame_equal(sql_stream._assemble(chunks, COLUMNS), expected)


def test_pooling_keeps_a_null_a_null_and_not_a_nan():
    """A categorical spells every missing value `nan`, whichever of None/nan came
    back. In an object column that is a value change with teeth: `None or 0` is 0
    and `nan or 0` is nan, and the coverage reports are full of
    `int(row["x"] or 0)` over aggregates that are NULL on an empty table."""
    chunk = pd.DataFrame({"n": [1, 2, 3], "s": [None, "a", "a"]}, dtype=object)
    sql_stream._pool_objects(chunk, {})
    assert chunk["s"].tolist() == [None, "a", "a"]
    assert chunk["s"].iloc[0] is None


def test_pooling_survives_a_column_it_cannot_hash():
    """A JSON column comes back as lists. Nothing to pool, and nothing to break."""
    chunk = _chunk(0, 10)
    chunk["blob"] = [[i] for i in range(10)]
    sql_stream._pool_objects(chunk, {})
    assert chunk["blob"].tolist() == [[i] for i in range(10)]


@pytest.fixture(params=["copy", "cursor"])
def path(request, monkeypatch):
    """Which Postgres read the live tests drive: the COPY-and-arrow read every
    eligible query now takes, or the server-side cursor it falls back to. Both
    must produce the frame pd.read_sql_query did."""
    monkeypatch.setattr(sql_stream, "COPY_READ", request.param == "copy")
    return request.param


def _chunk_size(monkeypatch, path: str, rows: int) -> None:
    """Chunks of about `rows` rows on either path: the cursor fetches by row
    count, the COPY read cuts its text by bytes (a probe row is ~30 of them)."""
    if path == "cursor":
        monkeypatch.setattr(sql_stream, "CHUNK_ROWS", rows)
    else:
        monkeypatch.setattr(sql_copy, "COPY_BLOCK_BYTES", rows * 30)


@pytest.fixture
def conn(path):
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


def test_it_really_did_take_more_than_one_chunk(conn, path, monkeypatch):
    """Guard on the guard: with the chunk above the row count every test below
    would exercise the single-chunk path and prove nothing about chunking."""
    _chunk_size(monkeypatch, path, 100)
    _fill(conn, 500)
    calls = []
    if path == "cursor":
        real = pd.DataFrame.from_records
        monkeypatch.setattr(
            pd.DataFrame, "from_records",
            staticmethod(lambda *a, **k: (calls.append(1), real(*a, **k))[1]))
    else:
        real = sql_stream._assemble
        monkeypatch.setattr(sql_stream, "_assemble",
                            lambda frames, cols: (calls.extend(frames),
                                                  real(frames, cols))[1])
    storage.read_sql(ORDERED, conn)
    assert len(calls) >= 4, f"expected several chunks, got {len(calls)}"


def test_chunk_boundaries_do_not_change_the_frame(conn, path, monkeypatch):
    """The same rows, read one chunk or many, must compare equal — values and
    dtypes both."""
    _fill(conn, 500)
    whole = storage.read_sql(ORDERED, conn)
    for chunk in (1, 7, 100, 499, 500, 501):
        _chunk_size(monkeypatch, path, chunk)
        pd.testing.assert_frame_equal(storage.read_sql(ORDERED, conn), whole)


def test_a_column_that_is_all_null_in_one_chunk_still_types_as_float(
        conn, path, monkeypatch):
    """The failure chunking could actually cause. `sparse` has values in the
    first 200 rows and NULL after, so with 100-row chunks three whole chunks
    type it as object; concat spreads that to the column and every downstream
    `.rolling()` on it breaks. infer_objects is what puts it back."""
    _fill(conn, 500, sparse_from=200)
    _chunk_size(monkeypatch, path, 100)
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


# ── the COPY read specifically ───────────────────────────────────────────────
#
# Everything above runs on both paths. What follows is what only the COPY read
# can get wrong: CSV's spelling of NULL, text that looks like CSV, floats that
# are not finite, and the queries and sessions it must hand to the cursor.

@pytest.fixture
def raw_conn():
    c = psycopg.connect(_live_dsn())
    yield c
    c.close()


def _both(conn, sql, params=None, monkeypatch=None):
    """``(copy read, cursor read)`` of one query, asserting which path each took."""
    took = []
    real = sql_copy.read

    def spy(*args):
        frame = real(*args)
        took.append(frame is not None)
        return frame

    monkeypatch.setattr(sql_copy, "read", spy)
    monkeypatch.setattr(sql_stream, "COPY_READ", True)
    fast = storage.read_sql(sql, conn, params=params)
    monkeypatch.setattr(sql_stream, "COPY_READ", False)
    slow = storage.read_sql(sql, conn, params=params)
    pd.testing.assert_frame_equal(fast, slow, check_exact=True)
    for i, name in enumerate(slow.columns):  # assert_frame_equal calls None == nan
        if slow.iloc[:, i].dtype == object:
            assert ([type(v) for v in fast.iloc[:, i]]
                    == [type(v) for v in slow.iloc[:, i]]), name
    return took[0], fast


@pytest.fixture
def typed(raw_conn):
    """Every type the COPY read accepts, with the values CSV makes awkward."""
    awkward = ["NA", "null", "", "N/A", "a,b", 'q"uote', "line\nbreak", "ção",
               " pad ", "NULL", "nan", "\\N"]
    floats = [0.1, float("nan"), float("inf"), -float("inf"), 1 / 3]
    numerics = ["0.1000000000000000055511151231257827", "12345678901234567890.5",
                "NaN", "-0", "3.14159"]
    with raw_conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE typed (i int, s smallint, b bigint, r real,"
                    " d double precision, n numeric, dt date, t text, v varchar(20),"
                    " c char(6), void_f double precision, void_t text, void_d date,"
                    " nan_f double precision, sparse int)")
        with cur.copy("COPY typed FROM STDIN") as cp:
            for k in range(120):
                row = (k, k % 300, 2**62 + k, k / 3, floats[k % 5], numerics[k % 5],
                       datetime.date(1999, 1, 1) + datetime.timedelta(days=37 * k),
                       awkward[k % len(awkward)], awkward[(k + 3) % len(awkward)],
                       "AB" if k % 2 else "ABCDEF", None, None, None,
                       float("nan"), None if k % 9 else k)
                if k == 5:                          # one row NULL but for its key
                    row = (k,) + (None,) * 14
                cp.write_row(row)
    raw_conn.commit()
    return raw_conn


def test_copy_read_matches_the_cursor_on_every_type_it_takes(typed, monkeypatch):
    took, df = _both(typed, "SELECT * FROM typed ORDER BY i", monkeypatch=monkeypatch)
    assert took, "every column here is a COPY type; the read must not fall back"
    # The spellings pyarrow would read as NULL by default are values here.
    assert {"NA", "null", "N/A", "NULL", "nan", ""} <= set(df["t"].dropna())
    assert df["t"].isna().sum() == 1                 # the one real NULL
    assert df["void_f"].dtype == object and df["void_f"].iloc[0] is None
    assert df["nan_f"].dtype == "float64"            # NaN is a value, not NULL


def test_copy_read_chunks_by_bytes_and_survives_a_row_longer_than_arrow_block(
        typed, monkeypatch):
    whole = storage.read_sql("SELECT * FROM typed ORDER BY i", typed)
    monkeypatch.setattr(sql_copy, "COPY_BLOCK_BYTES", 300)
    monkeypatch.setattr(sql_copy, "_ARROW_BLOCK_BYTES", 32)
    pd.testing.assert_frame_equal(
        storage.read_sql("SELECT * FROM typed ORDER BY i", typed), whole,
        check_exact=True)


@pytest.mark.parametrize("sql, params", [
    ("SELECT * FROM typed WHERE i >= :lo AND t <> :t ORDER BY i -- a comment",
     {"lo": 7, "t": "NA"}),
    ("SELECT t FROM typed WHERE t LIKE '%a%' ORDER BY i", None),
    ("SELECT t FROM typed WHERE t LIKE '%a%' AND i > ? ORDER BY i", [3]),
    ("WITH x AS (SELECT i, dt FROM typed) SELECT * FROM x ORDER BY i", None),
    ("SELECT count(*) AS n, max(dt) AS m FROM typed", None),
    ("SELECT * FROM typed WHERE i > 100000", None),
])
def test_copy_read_handles_the_shapes_of_query_read_sql_gets(typed, monkeypatch,
                                                             sql, params):
    took, _ = _both(typed, sql, params, monkeypatch=monkeypatch)
    assert took


@pytest.mark.parametrize("sql", [
    "SELECT now() AS ts",                            # timestamptz: not a COPY type
    "SELECT true AS flag",                           # bool
    "SELECT ARRAY[1, 2] AS a",
])
def test_copy_read_hands_other_types_to_the_cursor(typed, monkeypatch, sql):
    took, _ = _both(typed, sql, monkeypatch=monkeypatch)
    assert not took


@pytest.mark.parametrize("sql", [
    "WITH gone AS (DELETE FROM t RETURNING *) SELECT * FROM gone",
    "SELECT * FROM t FOR SHARE",
    "SELECT 1; SELECT 2",
    "INSERT INTO t VALUES (1) RETURNING *",
    "SHOW TimeZone",
])
def test_copy_read_never_wraps_what_is_not_a_plain_read(sql):
    """Refused on the text alone, before the connection is touched — a wrapper
    around a write or a second statement would change what runs."""
    assert sql_copy._plan(None, sql, None) is None


def test_copy_read_leaves_the_transaction_usable(typed):
    with typed.cursor() as cur:
        cur.execute("CREATE TEMP TABLE scratch (x int)")
        cur.execute("INSERT INTO scratch VALUES (1), (2)")
    # Uncommitted rows are visible: COPY runs on the caller's own connection.
    assert storage.read_sql("SELECT x FROM scratch ORDER BY x", typed)["x"].tolist() == [1, 2]
    with typed.cursor() as cur:
        cur.execute("SELECT count(*) FROM scratch")
        assert cur.fetchone()[0] == 2
    typed.rollback()


def test_copy_read_falls_back_when_dates_are_not_iso(typed, monkeypatch):
    with typed.cursor() as cur:
        cur.execute("SET DateStyle = 'SQL, DMY'")
    took, df = _both(typed, "SELECT i, dt FROM typed ORDER BY i",
                     monkeypatch=monkeypatch)
    assert not took
    assert df["dt"].iloc[0] == datetime.date(1999, 1, 1)


def test_sqlite_does_not_take_the_server_side_cursor(monkeypatch, tmp_path):
    """There is no server-side cursor to take on SQLite. It batches through
    pandas' own `chunksize` instead and lands in the same assembly, so the named
    cursor must never be reached."""
    monkeypatch.setattr(sql_stream, "_server_side",
                        lambda *a, **k: pytest.fail("sqlite reached _server_side"))
    conn = storage.get_connection(tmp_path / "probe.sqlite")
    try:
        conn.execute("CREATE TABLE t (a INTEGER, s TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)",
                         [(i, f"S{i % 3}") for i in range(250)])
        got = storage.read_sql("SELECT a, s FROM t ORDER BY a", conn)
        assert got["a"].tolist() == list(range(250))
        assert got["s"].tolist() == [f"S{i % 3}" for i in range(250)]
    finally:
        conn.close()


def test_a_sqlite_read_in_several_batches_matches_the_whole_one(tmp_path,
                                                                monkeypatch):
    """The same guarantee the Postgres path has, on the path a dev box takes."""
    conn = storage.get_connection(tmp_path / "probe.sqlite")
    try:
        conn.execute("CREATE TABLE t (a INTEGER, s TEXT, f REAL)")
        conn.executemany(
            "INSERT INTO t VALUES (?, ?, ?)",
            [(i, f"S{i % 5}", None if i > 300 else i / 4) for i in range(500)])
        sql = "SELECT a, s, f FROM t ORDER BY a"
        whole = storage.read_sql(sql, conn)
        for chunk in (1, 7, 100, 499, 500, 501):
            monkeypatch.setattr(sql_stream, "CHUNK_ROWS", chunk)
            pd.testing.assert_frame_equal(storage.read_sql(sql, conn), whole)
    finally:
        conn.close()


def test_an_empty_sqlite_result_keeps_its_columns(tmp_path):
    """Callers index an empty frame by name on the way out
    (universe.load_listing_intervals). The chunked iterator still yields one
    empty frame carrying them, which is where they come from."""
    conn = storage.get_connection(tmp_path / "probe.sqlite")
    try:
        conn.execute("CREATE TABLE t (a INTEGER, s TEXT)")
        empty = storage.read_sql("SELECT a, s FROM t", conn)
        assert len(empty) == 0 and list(empty.columns) == ["a", "s"]
    finally:
        conn.close()
