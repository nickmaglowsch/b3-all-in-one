"""Read a query through ``COPY … TO STDOUT`` and pyarrow instead of a cursor.

sql_stream fixed the peak of the big Postgres read; it did not fix the time.
Even through a server-side cursor the driver turns every cell into a Python
object before pandas sees it, and on the panel read that decoding was ~90% of
the wall clock: Postgres executes the query in 0.7s and the read took 13s.

So a query whose columns are all of a few plain types is read with ``COPY
(query) TO STDOUT (FORMAT csv)`` on the caller's own connection — same
transaction, same snapshot, uncommitted rows included — and pyarrow parses the
text in C++ straight into typed columns. Each chunk then goes through
sql_stream's pooling and assembly exactly as a fetched chunk does, so the frame
is the cursor path's frame, dtypes and object identities included. Measured on
3.28M panel rows (median of three):

    server-side cursor     13.2s
    COPY + pyarrow          4.7s   same peak

What is left is mostly the server rendering floats as text.

**Why CSV, not binary.** Binary COPY costs the server about half as much, but
its rows are variable-length, so finding the fields is a sequential scan — a
Python loop per row, or a compiled dependency to avoid one. A driver that does
it in C++ (ADBC) measured no faster end to end and needs a second connection,
which cannot see the caller's transaction.

**Why it chunks by hand.** libpq returns COPY data one row per message, so
whole-row chunks are free to cut: a chunk is parsed with ``read_csv`` on a
worker thread (arrow drops the GIL) while the next is drained. pyarrow's own
streaming reader was measured first; its read-ahead held ~60 MB more at peak
than the cursor path did, which is the one number this module may not move.

**When it declines.** A column type outside ``_COPY_TYPES`` (bool, timestamp,
json, arrays…), anything that is not a plain SELECT, or a session whose text
output the parser cannot assume. Declining returns None and the caller takes
the cursor path, where every query always went.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import sql_stream

# Bytes of CSV per chunk. A chunk is converted, pooled and kept as one frame, so
# this plays sql_stream.CHUNK_ROWS' part: 16 MB is ~250k panel rows.
COPY_BLOCK_BYTES = 16 << 20

# Arrow's own parse block inside a chunk; several per chunk is what lets it
# parse one chunk on every core.
_ARROW_BLOCK_BYTES = 4 << 20

# Postgres type OID -> the arrow type its CSV text is parsed as. Only types whose
# text pyarrow parses to exactly what psycopg builds from the same text (psycopg
# reads the text protocol too): dates, strings, ints, and floats — numeric
# included, since coerce_float turns its Decimals into floats on the cursor path.
_COPY_TYPES = {
    1082: "date32",                                         # date
    25: "string", 1043: "string", 1042: "string", 19: "string",  # text, varchar, char, name
    700: "float64", 701: "float64", 1700: "float64",        # real, double, numeric
    21: "int64", 23: "int64", 20: "int64",                  # smallint, integer, bigint
}

# What gets wrapped: a plain read. COPY (…) takes a SELECT, but a data-modifying
# CTE or a locking clause changes meaning inside a wrapper, and a second
# statement would not survive one at all.
_PLAIN_READ = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_NOT_PLAIN = re.compile(r";|\b(insert|update|delete|merge|for\s+share)\b",
                        re.IGNORECASE)


def read(conn, sql: str, params=None) -> pd.DataFrame | None:
    """The query's frame, or None when it does not qualify for COPY.

    ``sql`` is already dialect-translated (storage._xlate), as sql_stream
    expects; ``params`` are inlined by psycopg, since COPY takes no binds.
    """
    plan = _plan(conn, sql, params)
    if plan is None:
        return None
    statement, names, types = plan
    parse = _parser(names, types)
    out = _Collector(names, types)
    # Chunk k parses on the worker while chunk k+1 drains; one in flight keeps
    # the order and bounds what is held to two chunks of text. The per-row work
    # is one append into a bytearray — not a list of rows joined later: each row
    # psycopg yields is a view on a libpq buffer, and keeping 100k of them alive
    # measured 70% slower than the bare iteration, where appending is within 5%.
    with ThreadPoolExecutor(max_workers=1) as worker, \
            conn.cursor() as cur, cur.copy(statement) as copy:
        pending, buf = None, bytearray()
        for row in copy:
            buf += row
            if len(buf) >= COPY_BLOCK_BYTES:
                if pending is not None:
                    out.add(pending.result())
                pending, buf = worker.submit(parse, buf), bytearray()
        if pending is not None:
            out.add(pending.result())
        if buf:
            out.add(parse(buf))
    return out.frame()


def _plan(conn, sql: str, params):
    """``(statement, column names, arrow types)``, or None to decline.

    The text is judged before the connection is touched. Then the session: a
    non-UTF-8 client encoding or a non-ISO DateStyle would change the text the
    parser reads. Then the columns, described by running the query under
    ``LIMIT 0``: every one must be a ``_COPY_TYPES`` type, and no name may repeat.
    """
    if not _PLAIN_READ.match(sql) or _NOT_PLAIN.search(sql):
        return None
    info = conn.info
    if (info.encoding or "").lower().replace("-", "") != "utf8":
        return None
    if not (info.parameter_status("DateStyle") or "ISO").startswith("ISO"):
        return None

    import psycopg
    query = (sql if params is None
             else psycopg.ClientCursor(conn).mogrify(sql, params))
    # A newline before each closing paren: a query ending in a `-- comment`
    # would otherwise swallow the rest of the wrapper.
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM (\n{query}\n) AS _q LIMIT 0")
        desc = cur.description
    names = [d.name for d in desc]
    types = [_COPY_TYPES.get(d.type_code) for d in desc]
    if None in types or len(set(names)) != len(names):
        return None
    return f"COPY (\n{query}\n) TO STDOUT WITH (FORMAT csv)", names, types


def _parser(names: list, types: list):
    """``bytes of whole CSV rows -> arrow Table`` for these columns.

    NULL is CSV's unquoted empty field and nothing else — pyarrow's default null
    spellings ("NA", "null", "N/A"…) are real tickers and company names here —
    and a quoted "" stays the empty string it was.
    """
    import pyarrow as pa
    import pyarrow.csv as pacsv

    parse = pacsv.ParseOptions(newlines_in_values="string" in types)
    convert = pacsv.ConvertOptions(
        column_types={n: getattr(pa, t)() for n, t in zip(names, types)},
        null_values=[""], strings_can_be_null=True,
        quoted_strings_can_be_null=False)

    def run(buf: bytearray):
        source = pa.py_buffer(buf)
        blocks = pacsv.ReadOptions(column_names=names,
                                   block_size=_ARROW_BLOCK_BYTES)
        try:
            return pacsv.read_csv(source, read_options=blocks,
                                  parse_options=parse, convert_options=convert)
        except pa.ArrowInvalid as exc:
            # A row longer than arrow's block cannot be split across two. The
            # chunk is whole rows, so one block the size of the chunk always fits.
            if "straddl" not in str(exc):
                raise
            whole = pacsv.ReadOptions(column_names=names, use_threads=False,
                                      block_size=len(buf) + 1)
            return pacsv.read_csv(source, read_options=whole,
                                  parse_options=parse, convert_options=convert)

    return run


class _Collector:
    """Parsed chunks, turned into the frames the cursor path would have built."""

    def __init__(self, names: list, types: list) -> None:
        self.names, self.types = names, types
        self.dates = {n for n, t in zip(names, types) if t == "date32"}
        self.frames: list[pd.DataFrame] = []
        self.pools: dict = {}
        self.rows = 0
        self.present = [0] * len(names)          # non-NULL values per column

    def add(self, table) -> None:
        self.rows += table.num_rows
        data = {}
        for i, (name, column) in enumerate(zip(self.names, table.columns)):
            self.present[i] += table.num_rows - column.null_count
            data[name] = (_date_objects(column.combine_chunks(), self.pools, name)
                          if name in self.dates else column.to_pandas())
        chunk = pd.DataFrame(data, columns=self.names)
        sql_stream._pool_objects(chunk, self.pools, skip=self.dates)
        self.frames.append(chunk)

    def frame(self) -> pd.DataFrame:
        frame = sql_stream._assemble(self.frames, self.names)
        # A column NULL on every row has no type to infer on the cursor path and
        # comes back as object Nones; arrow would type it from the schema.
        # Counted on arrow's nulls, not pandas' isna, so a column of real NaNs
        # keeps its float dtype the way the cursor path keeps it.
        for name, present in zip(self.names, self.present):
            if self.rows and not present:
                frame[name] = pd.Series([None] * self.rows, dtype=object,
                                        index=frame.index)
        return frame


def _date_objects(column, pools: dict, name: str):
    """An arrow date column as pooled ``datetime.date`` objects.

    The cursor path ends with one object per distinct date (``_pool_objects``);
    pandas' arrow conversion would mint one per row first and leave the pooling
    to hash them all back down. Dictionary-encoding in arrow gets there directly:
    the distinct dates become objects once, and the rows are an index into them.
    """
    import numpy as np
    import pyarrow.compute as pc

    encoded = pc.dictionary_encode(column)
    distinct = encoded.dictionary.to_pylist()
    pool = pools.setdefault(name, {})
    if pool is not None:
        distinct = [pool.setdefault(v, v) for v in distinct]
        if len(pool) > sql_stream._POOL_MAX:
            pools[name] = None
    values = np.empty(len(distinct) + 1, dtype=object)
    values[:-1] = distinct
    values[-1] = None                      # where a NULL row's index points
    codes = encoded.indices.fill_null(len(distinct)).to_numpy()
    return values[codes]
