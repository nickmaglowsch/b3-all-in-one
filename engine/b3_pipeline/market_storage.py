"""Market-plane schema and writers for the markets that are not B3.

`prices` stays B3-only and untouched, and these tables sit beside it. That is the
same call `storage.py` already made for `external_prices` (see the long comment
above SCHEMA_EXTERNAL_PRICES): the provenance, the rebuild semantics and the
readers all differ, and "one writer's need should not tax every reader". Here the
argument is stronger — `quotation_factor`, `split_adj_*` and `isin_code NOT NULL`
are COTAHIST concepts that mean nothing off B3, and `prices` is PRIMARY KEY
(ticker, date), so adding a market dimension to it means a primary-key rebuild:
an ACCESS EXCLUSIVE lock on the largest table in the database, issued from a
migrator that is otherwise additive-only.

Nothing here appears in `init_db(rebuild=True)`'s drop list, deliberately, and for
the reason `external_prices` doesn't either: a rebuild assumes the rows can be
fetched again. The US price archive is built forward day by day and cannot be, and
`instruments` carries the listing/delisting intervals observed from it — the
survivorship record itself. Crypto alone could be re-downloaded; that is not
enough to drop the table the other two share. Use a targeted DELETE when one
market really must be rebuilt.

The helpers from `storage` are imported inside the functions, not at module scope:
`storage` imports this module to build its DDL list, so a module-level import back
would be a cycle.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


# ── Multi-market plane (US equities, crypto) ──────────────────────────────────
#
# `prices` stays B3-only and untouched. The same reasoning that made
# `external_prices` a sibling table rather than rows inside `prices` (see the
# comment above SCHEMA_EXTERNAL_PRICES) applies harder here: quotation_factor,
# split_adj_* and `isin_code NOT NULL` are COTAHIST concepts, and `prices` is
# PRIMARY KEY (ticker, date) — adding a market dimension to it means a PK
# rebuild, which the additive-only migrator below cannot do and which would take
# an ACCESS EXCLUSIVE lock on the largest table in the database.
#
# So: one security master covering every market, and one price table for the
# markets that are not B3. B3 rows are backfilled into `instruments` from
# cvm_companies/company_tickers_pit so the universe logic has a single shape.

# One row per instrument that ever existed, alive or dead. This table is what
# makes a point-in-time universe possible: `listing_date`/`delisting_date` are
# the validity interval a backtest slices by date, so a name that died mid-window
# is in the universe before it died and out of it afterwards — the opposite of
# dropping it, which is what a survivorship-biased dataset does for you.
#
# `first_seen`/`last_seen` are observed from the price archive; listing_date /
# delisting_date are claimed by the source where one says so, and fall back to
# the observed bounds where none does (crypto has no delisting announcement in
# the archive — a pair whose klines stop IS the delisting).
SCHEMA_INSTRUMENTS = """
CREATE TABLE IF NOT EXISTS instruments (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    venue TEXT,
    currency TEXT NOT NULL,
    kind TEXT,
    name TEXT,
    -- Whichever identifier the market actually has; the others stay NULL.
    isin_code TEXT,
    cik TEXT,
    cnpj TEXT,
    listing_date DATE,
    delisting_date DATE,
    delist_reason TEXT,
    -- Observed bounds from the price archive, not claimed by any source.
    first_seen DATE,
    last_seen DATE,
    -- Set when the instrument is known to have gone to zero rather than merely
    -- stopped trading here. The simulator liquidates at the last price by
    -- default and at zero when this is set.
    is_worthless INTEGER DEFAULT 0,
    is_test_issue INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market, symbol)
);
"""

INDEX_INSTRUMENTS_MARKET = (
    "CREATE INDEX IF NOT EXISTS idx_instruments_market ON instruments(market);"
)
INDEX_INSTRUMENTS_CIK = (
    "CREATE INDEX IF NOT EXISTS idx_instruments_cik ON instruments(cik);"
)
INDEX_INSTRUMENTS_DELISTING = (
    "CREATE INDEX IF NOT EXISTS idx_instruments_delisting"
    " ON instruments(market, delisting_date);"
)

# Daily bars for every market that is not B3.
#
# Two currency columns per price, not one: the archive keeps what the venue
# published (USD for a US listing, USDT for a Binance spot pair) and a BRL
# projection materialised through PTAX at ingest. The native column is the
# faithful record; the BRL column is what the tax engine and the portfolio need,
# and `fx_rate` is kept alongside so any BRL figure can be traced back to the
# rate that produced it (the "never invent a number" rule — a BRL close with no
# recoverable rate is a number nobody can check).
SCHEMA_MARKET_PRICES = """
CREATE TABLE IF NOT EXISTS market_prices (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    -- Financial volume in the quote currency (Binance kline column 7, the US
    -- feed's turnover), matching what prices.volume holds for B3. NOT a share
    -- or coin count — the liquidity filters read this.
    volume REAL,
    split_adj_close REAL,
    adj_close REAL,
    close_brl REAL,
    adj_close_brl REAL,
    fx_rate REAL,
    source TEXT NOT NULL,
    PRIMARY KEY (market, symbol, date)
);
"""

INDEX_MARKET_PRICES_DATE = (
    "CREATE INDEX IF NOT EXISTS idx_market_prices_date ON market_prices(market, date);"
)


# Appended to storage.init_db's ddl_statements, so these run in the same pass and
# through the same _pg_ddl translation as every other CREATE in the plane.
DDL = [
    SCHEMA_INSTRUMENTS,
    INDEX_INSTRUMENTS_MARKET,
    INDEX_INSTRUMENTS_CIK,
    INDEX_INSTRUMENTS_DELISTING,
    SCHEMA_MARKET_PRICES,
    INDEX_MARKET_PRICES_DATE,
]

def _upsert_frame(conn, table: str, df: pd.DataFrame, key: list[str],
                  cols: list[str], stage_coltypes: str, stage_name: str,
                  update_cols: list[str] | None = None,
                  merge: dict[str, str] | None = None) -> int:
    """Upsert `df[cols]` into `table` on `key`, on either backend.

    Factored out because the three multi-market tables want exactly the pattern
    upsert_prices already uses — COPY into a temp table then one INSERT ...
    ON CONFLICT on Postgres, executemany on SQLite — and repeating it three more
    times is how the two paths drift apart. The `ord`/DISTINCT ON dedup and the
    IS DISTINCT FROM guard carry over for the same reasons documented there: a
    batch may legally carry the same key twice (last wins), and re-ingesting an
    unchanged period should touch no rows.

    `update_cols` narrows what an existing row gets overwritten with, without
    narrowing the INSERT — a new row still lands with every column the table
    has, NULL where the caller knew nothing. That difference is the whole point:
    a writer that only knows some of the columns must be able to say so, or it
    erases what another writer knew. See `upsert_instruments`.

    `merge` replaces `excluded.<col>` with an expression for the columns it
    names — how a column that must only ever move one way says so. Plain SQL
    that both backends evaluate identically; see `_OBSERVED_BOUNDS`.
    """
    from . import storage

    if df.empty:
        return 0

    value_cols = [c for c in (update_cols if update_cols is not None else cols)
                  if c not in key]
    if not value_cols:
        raise ValueError(
            f"upsert into {table} was given nothing to write beyond the key "
            f"{key} — an INSERT that can only ever conflict is a caller bug, "
            "not a no-op worth hiding.")

    new = {c: (merge or {}).get(c, f"excluded.{c}") for c in value_cols}
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = {new[c]}" for c in value_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "      # noqa: S608
        f"ON CONFLICT({', '.join(key)}) DO UPDATE SET {updates}"
    )
    records = storage.df_records(df[cols])
    rows = [[r[c] for c in cols] for r in records]

    if storage.is_postgres(conn):
        storage._copy_into_temp(
            conn, stage_name, [*cols, "ord"],
            f"{stage_coltypes}, ord BIGINT",
            ([*r, i] for i, r in enumerate(rows)),
        )
        collist = ", ".join(cols)
        keylist = ", ".join(key)
        unchanged = " OR ".join(
            f"{table}.{c} IS DISTINCT FROM {new[c]}" for c in value_cols)
        storage.execute(conn, f"""
            INSERT INTO {table} ({collist})
            SELECT {collist} FROM (
                SELECT DISTINCT ON ({keylist}) {collist}
                FROM {stage_name} ORDER BY {keylist}, ord DESC
            ) deduped
            ON CONFLICT ({keylist}) DO UPDATE SET {updates}
            WHERE ({unchanged})
        """)                                                                     # noqa: S608
    else:
        storage.executemany(conn, sql, rows)
    conn.commit()
    return len(rows)


INSTRUMENT_COLUMNS = [
    "market", "symbol", "venue", "currency", "kind", "name",
    "isin_code", "cik", "cnpj",
    "listing_date", "delisting_date", "delist_reason",
    "first_seen", "last_seen", "is_worthless", "is_test_issue",
]

_INSTRUMENTS_STAGE_TYPES = (
    "market TEXT, symbol TEXT, venue TEXT, currency TEXT, kind TEXT, name TEXT,"
    " isin_code TEXT, cik TEXT, cnpj TEXT, listing_date DATE, delisting_date DATE,"
    " delist_reason TEXT, first_seen DATE, last_seen DATE,"
    " is_worthless BIGINT, is_test_issue BIGINT"
)

# The observed bounds are sightings, so they only ever widen: `first_seen` is
# the earliest any writer saw the instrument trade and `last_seen` the latest.
# Enforced here rather than trusted to every writer because two of them already
# got it wrong — the symbol directory reset a returning symbol's `first_seen` to
# the day it came back, and the frozen WIKI file wrote a 2018 `last_seen` over
# the directory's 2026 one, which is the date a later delisting is stamped with.
#
# CASE rather than MIN/MAX/LEAST/GREATEST because those disagree about NULL
# between the two backends (SQLite's scalar MAX returns NULL if any argument is;
# Postgres' GREATEST ignores them). Here a NULL on either side keeps the other,
# so an unknown bound never erases a known one. Dates compare correctly on both:
# DATE on Postgres, ISO-8601 text on SQLite.
_OBSERVED_BOUNDS = {
    col: (f"CASE WHEN instruments.{col} IS NULL OR excluded.{col} {op}"
          f" instruments.{col} THEN excluded.{col} ELSE instruments.{col} END")
    for col, op in (("first_seen", "<"), ("last_seen", ">"))
}


def upsert_instruments(conn, df: pd.DataFrame) -> int:
    """Upsert rows into the security master, touching only the columns given.

    A new row lands with every column the table has, NULL where the caller knew
    nothing. An *existing* row is updated only on the columns actually present
    in `df`, and this is the whole reason the function is written this way.

    Four writers share this table and none of them knows all of it. The
    consolidated symbol directory knows name/venue/kind and the observed bounds;
    the SEC ingest knows the CIK; the listing-status feed knows the listing
    interval; the B3 backfill knows ISIN and CNPJ. Filling every absent column
    with NULL and overwriting unconditionally — which is what this did — meant
    the nightly directory snapshot erased the CIK the weekly SEC ingest had
    written, so `instruments.cik` (the join between US fundamentals and US
    prices) was null six days out of seven.
    """
    if df.empty:
        logger.warning("No instrument records to upsert")
        return 0
    supplied = [c for c in INSTRUMENT_COLUMNS if c in df.columns]
    # `currency` is NOT NULL, so a first sighting without it fails deep inside
    # the driver with an IntegrityError naming a column the caller never
    # mentioned. Say it here instead.
    if "currency" not in supplied:
        raise ValueError(
            "upsert_instruments needs `currency` on every row: it is NOT NULL, "
            "and a writer that does not know what the venue quotes in cannot "
            "create the row. Columns given: " + str(sorted(df.columns)))
    df = df.copy()
    for col in INSTRUMENT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in ("listing_date", "delisting_date", "first_seen", "last_seen"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    # The two flags are NOT NULL-in-spirit (DEFAULT 0), and an explicit NULL
    # bypasses a column default — so they are filled for the INSERT even when
    # the caller did not supply them. They stay out of `supplied`, so an
    # unsupplied flag is never written over an existing row.
    for col in ("is_worthless", "is_test_issue"):
        df[col] = df[col].fillna(0).astype(int)
    n = _upsert_frame(
        conn, "instruments", df, ["market", "symbol"], INSTRUMENT_COLUMNS,
        _INSTRUMENTS_STAGE_TYPES, "_instr_stage", update_cols=supplied,
        merge=_OBSERVED_BOUNDS,
    )
    logger.info(f"Upserted {n:,} instrument records")
    return n


MARKET_PRICE_COLUMNS = [
    "market", "symbol", "date", "open", "high", "low", "close", "volume",
    "split_adj_close", "adj_close", "close_brl", "adj_close_brl", "fx_rate", "source",
]

_MARKET_PRICES_STAGE_TYPES = (
    "market TEXT, symbol TEXT, date DATE, open DOUBLE PRECISION,"
    " high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,"
    " volume DOUBLE PRECISION, split_adj_close DOUBLE PRECISION,"
    " adj_close DOUBLE PRECISION, close_brl DOUBLE PRECISION,"
    " adj_close_brl DOUBLE PRECISION, fx_rate DOUBLE PRECISION, source TEXT"
)


def upsert_market_prices(conn, df: pd.DataFrame) -> int:
    """Upsert daily bars for a non-B3 market.

    Validation is deliberately weaker than upsert_prices': that one drops
    zero-volume rows because a COTAHIST row with no volume did not trade. Here a
    zero-volume session is real — an illiquid US small cap or a crypto pair in
    its dying days can print a bar with no turnover, and dropping those is
    exactly the silent survivorship hole this table exists to close. Only
    non-positive closes and internally inconsistent OHLC are rejected.
    """
    if df.empty:
        logger.warning("No market price records to upsert")
        return 0

    df = df.copy()
    for col in MARKET_PRICE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    before = len(df)
    df = df[df["close"].astype(float) > 0]
    bad = df["high"].notna() & df["low"].notna() & (
        (df["high"] < df["low"]) | (df["close"] < df["low"]) | (df["close"] > df["high"]))
    df = df[~bad]
    dropped = before - len(df)
    if dropped:
        logger.warning(f"Dropped {dropped:,} market price rows with invalid close/OHLC")

    n = _upsert_frame(
        conn, "market_prices", df, ["market", "symbol", "date"], MARKET_PRICE_COLUMNS,
        _MARKET_PRICES_STAGE_TYPES, "_mpx_stage",
    )
    logger.info(f"Upserted {n:,} market price records")
    return n


def delete_market_prices_from_other_sources(conn, market: str, symbol: str,
                                            source_prefix: str) -> int:
    """Drop one symbol's bars that some *other* source wrote. Returns rows gone.

    The targeted DELETE this module's header note reserves, and it exists for
    one caller and one reason. `market_prices` is keyed `(market, symbol, date)`
    and its `close` carries whatever convention its writer used — WIKI ships a
    raw close, Stooq a split-adjusted one. A symbol both of them wrote therefore
    holds two conventions in one column, joined at whatever date the two
    coverages happen to meet, and the join is a fabricated return: a split-sized
    jump, which is precisely what `MARKETS["US"].glitch_down` is built to mask.

    Upserting cannot fix that, because it only overwrites the dates the new
    source also has. A symbol whose new series starts later than the old one
    leaves the old rows in front of it — still raw, still spliced, and now
    invisible because every date the new run touched looks correct. So the old
    rows are removed rather than written over.

    Deliberately not a general "rebuild this market": it takes one symbol, and
    it is scoped by `source_prefix` so a source can never delete its own rows —
    a re-run stays idempotent, and a caller that names the wrong prefix deletes
    nothing rather than the plane.
    """
    from . import storage

    cur = storage.execute(
        conn,
        "DELETE FROM market_prices"
        " WHERE market = ? AND symbol = ?"
        "   AND (source IS NULL OR source NOT LIKE ?)",
        (market, symbol, f"{source_prefix}%"))
    n = cur.rowcount or 0
    conn.commit()
    if n:
        logger.info(f"{market}/{symbol}: dropped {n:,} bars from another "
                    f"source before writing {source_prefix}")
    return n
