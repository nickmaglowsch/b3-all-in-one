"""Build a DataFrame from a Postgres query without materialising it twice.

``pd.read_sql_query`` has no fast path for a bare DBAPI connection — pandas says
so itself, with the warning ``storage.read_sql`` suppresses. It falls back to
``cursor.fetchall()``, so the whole result becomes a list of Python tuples of
Python floats *before* the frame is built, on top of the buffer libpq already
holds client-side. Two full copies of the answer, neither of them the answer.

That is affordable for the small reads in this package and ruinous for the one
that matters. ``load_market_hlc_data`` pulls every B3 bar in the window, and a
default backtest window is 2005..today. Measured against 3.83M rows (700 tickers
x 21 years, a 529 MB table):

    pd.read_sql_query -> fetchall     24.0s   peak RSS 3102 MB
    server-side cursor, this module   13.0s   peak RSS  941 MB

The frame those 3.1 GB produce is 389 MB, and the six wide frames the backtest
actually keeps are 184 MB. So ~2.7 GB of the peak was transient overhead — and
it was the whole problem: the worker runs two of these at once, the API spawns
one advise_weights subprocess per portfolio sleeve, and no container in either
compose file had a memory limit. The box went to swap and every request stalled,
including /health, which is why a running backtest read as "the API is down".

Streaming does not make the data smaller; it stops us holding three
representations of it at the same moment.

Two more representations went the same way when the US plane arrived, where the
read is an order of magnitude bigger than B3's. ``_pool_objects`` collapses the
per-row ``str`` and ``datetime.date`` instances the driver mints onto one object
per distinct value, and ``_assemble`` concatenates the chunks a column at a time
so the answer never exists twice. Neither changes a value or a dtype.
"""
from __future__ import annotations

import itertools

import pandas as pd

# Rows per FETCH. Big enough that the round trips disappear into the scan (the
# 3.8M-row read above is 39 of them), small enough that a chunk's tuples are a
# rounding error against the frame being accumulated.
CHUNK_ROWS = 100_000

# Server-side cursors are named, and a name is only free once its cursor is
# closed. read_dataframe always closes before returning, so a constant would do
# — until a caller streams two results at once and gets a DuplicateCursor it
# cannot explain. A counter costs nothing and removes the question.
_names = itertools.count()

# Stop pooling a column once it has this many distinct values. Pooling pays for
# itself on a column with few distinct values repeated across millions of rows —
# a symbol, a date, a market key. On a column that is distinct per row the pool
# would grow to the row count and cost more than it saves, so it gives up.
_POOL_MAX = 200_000


def _pool_objects(chunk: pd.DataFrame, pools: dict) -> None:
    """Point each object column's values at one shared object per distinct value.

    The driver builds a *new* Python object per row: a 30-million-row read of the
    US plane comes back with 30 million separate ``str`` instances of about a
    dozen distinct four-character symbols, and 30 million separate
    ``datetime.date`` instances of about 6,500 distinct days. That is gigabytes
    of duplicated objects, and it is the single largest thing in the process
    while the read is running — before the caller has had a chance to convert the
    columns to something compact.

    Nothing observable changes: the values are equal to what they were (they
    *are* the objects the first chunk produced), the column keeps its object
    dtype, and pandas never sees the categorical this borrows its factorize from.
    Only the object count drops, from one per row to one per distinct value.
    """
    for name in chunk.columns:
        if chunk[name].dtype != object:
            continue
        pool = pools.setdefault(name, {})
        if pool is None:                      # gave up on this column earlier
            continue
        try:
            cats = chunk[name].astype("category")
        except TypeError:                     # unhashable values — nothing to pool
            pools[name] = None
            continue
        canonical = [pool.setdefault(v, v) for v in cats.cat.categories]
        if len(pool) > _POOL_MAX:
            pools[name] = None
            continue
        pooled = cats.cat.rename_categories(
            pd.Index(canonical, dtype=object)).astype(object)
        # A categorical spells every missing value `nan`, whichever of None/nan
        # the driver handed back. That distinction is visible in an object column
        # — `None or 0` is 0 and `nan or 0` is nan, which is the difference
        # between a coverage report reading zero and one raising — so the
        # original values are put back wherever there was no category to map.
        missing = cats.isna()
        chunk[name] = pooled.where(~missing, chunk[name]) if missing.any() else pooled


def _assemble(frames: list, columns: list) -> pd.DataFrame:
    """The chunk frames as one frame, one column at a time.

    ``pd.concat(frames)`` builds the whole result while every chunk is still
    alive, so the largest read in the process briefly exists twice. Consuming one
    column across all chunks and releasing each chunk's slice as it goes peaks at
    the result plus one column instead.

    Chunking is the one thing that can move a dtype: a column that happens to be
    all-NULL across a whole chunk types as object there, and concatenating that
    with float chunks spreads object to the column. infer_objects undoes exactly
    that and nothing else — it re-reads object columns numpy could type, and
    leaves the ones it could not (date objects, strings, Decimals) alone, which
    is where read_sql_query leaves them too.
    """
    if not frames:
        return pd.DataFrame(columns=columns)
    if len(frames) == 1:
        return frames[0]
    out = {}
    for name in columns:
        parts = [frame.pop(name) for frame in frames]
        out[name] = pd.concat(parts, ignore_index=True)
        del parts
    return pd.DataFrame(out, columns=columns).infer_objects()


def read_chunked(chunks, columns: list | None = None) -> pd.DataFrame:
    """Assemble pandas' own ``chunksize=`` iterator with the same two savings.

    The SQLite path has no server-side cursor to take, but it has the same
    problem: ``pd.read_sql_query`` without ``chunksize`` calls ``fetchall`` and
    builds a Python tuple per row — on a 12-million-bar read that is several GB
    of tuples and boxed floats before the frame exists at all, dwarfing the frame
    itself. Handing pandas a chunksize makes it fetch in batches; this assembles
    the batches without holding two copies or a per-row object.

    A result with no rows still yields one empty frame carrying the column names,
    which callers index by name on the way out (universe.load_listing_intervals),
    so the columns survive without a second source for them.
    """
    frames: list[pd.DataFrame] = []
    pools: dict = {}
    for chunk in chunks:
        if columns is None:
            columns = list(chunk.columns)
        _pool_objects(chunk, pools)
        frames.append(chunk)
    return _assemble(frames, columns or [])


def read_dataframe(conn, sql: str, params=None,
                   postgres: bool = True) -> pd.DataFrame:
    """``pd.read_sql_query(sql, conn, params=params)``, fetched in batches.

    Same frame, same dtypes (see ``_assemble``), a fraction of the peak memory.
    ``sql`` is expected to be already dialect-translated, and ``postgres`` to say
    which dialect it was translated for — ``storage.read_sql`` owns both.

    Postgres takes a server-side cursor; SQLite has none to take, so it uses
    pandas' own ``chunksize``, which fetches in batches the same way. Both land in
    the same assembly. The dialect branch is here rather than in the caller
    because "read this query without materialising it twice" is one job, and
    splitting it left the SQLite half doing the thing this module exists to stop.
    """
    if not postgres:
        return read_chunked(
            pd.read_sql_query(sql, conn, params=params, chunksize=CHUNK_ROWS))
    return _server_side(conn, sql, params)


def _server_side(conn, sql: str, params=None) -> pd.DataFrame:
    """The Postgres path: one named cursor, fetched CHUNK_ROWS at a time.

    Passing ``params`` only when it is not None mirrors pandas, and matters:
    psycopg scans a query for placeholders exactly when it is given parameters,
    so a literal ``%`` in an unparameterised query must not be handed the
    parameter path (see storage._xlate and tests/test_sql_placeholders.py).
    """
    frames: list[pd.DataFrame] = []
    columns: list[str] = []
    pools: dict = {}
    with conn.cursor(name=f"b3_stream_{next(_names)}") as cur:
        cur.itersize = CHUNK_ROWS
        cur.execute(sql) if params is None else cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        while rows := cur.fetchmany(CHUNK_ROWS):
            # from_records with coerce_float is precisely what pandas does to
            # turn fetched rows into a frame (io.sql._wrap_result), so a
            # single-chunk read is byte-for-byte the old result.
            chunk = pd.DataFrame.from_records(
                rows, columns=columns, coerce_float=True)
            del rows                        # the tuples are the frame's now
            _pool_objects(chunk, pools)
            frames.append(chunk)
    return _assemble(frames, columns)
