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


def read_dataframe(conn, sql: str, params=None) -> pd.DataFrame:
    """``pd.read_sql_query(sql, conn, params=params)``, fetched in chunks.

    Same frame, same dtypes (see below), a third of the peak memory. ``sql`` is
    expected to be already dialect-translated — ``storage.read_sql`` owns that.

    Passing ``params`` only when it is not None mirrors pandas, and matters:
    psycopg scans a query for placeholders exactly when it is given parameters,
    so a literal ``%`` in an unparameterised query must not be handed the
    parameter path (see storage._xlate and tests/test_sql_placeholders.py).
    """
    frames: list[pd.DataFrame] = []
    columns: list[str] = []
    with conn.cursor(name=f"b3_stream_{next(_names)}") as cur:
        cur.itersize = CHUNK_ROWS
        cur.execute(sql) if params is None else cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        while rows := cur.fetchmany(CHUNK_ROWS):
            # from_records with coerce_float is precisely what pandas does to
            # turn fetched rows into a frame (io.sql._wrap_result), so a
            # single-chunk read is byte-for-byte the old result.
            frames.append(
                pd.DataFrame.from_records(rows, columns=columns, coerce_float=True)
            )
    if not frames:
        return pd.DataFrame(columns=columns)
    if len(frames) == 1:
        return frames[0]
    # Chunking is the one thing that can move a dtype: a column that happens to
    # be all-NULL across a whole chunk types as object there, and concat spreads
    # that to the column. infer_objects undoes exactly that and nothing else —
    # it re-reads object columns numpy could type, and leaves the ones it could
    # not (date objects, strings, Decimals) alone, which is where read_sql_query
    # leaves them too.
    return pd.concat(frames, ignore_index=True).infer_objects()
