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

# US point-in-time fundamentals, from SEC XBRL.
#
# Deliberately column-for-column comparable with fundamentals_pit so the readers
# in backtests/core/data.py (_query_fundamentals_pit, _pit_frame) work on both
# without a second implementation. Two differences, both forced by the source:
#   - the key is `cik`, not `cnpj`;
#   - there is no filing_version. In XBRL a restatement is a new accession with a
#     later `filed`, which is exactly the semantics fundamentals_pit already
#     relies on: versions are never deduplicated, so a restatement only becomes
#     visible after its own filing_date.
# Values are in the units the filer reported (USD, raw), NOT the thousands that
# CVM reports — see the note above fundamentals_monthly.
SCHEMA_FUNDAMENTALS_PIT_US = """
CREATE TABLE IF NOT EXISTS fundamentals_pit_us (
    fact_id TEXT NOT NULL PRIMARY KEY,
    cik TEXT NOT NULL,
    ticker TEXT,
    period_end DATE NOT NULL,
    filing_date DATE NOT NULL,
    accn TEXT NOT NULL,
    form TEXT,
    fiscal_year INTEGER,
    fiscal_period TEXT,
    revenue REAL,
    net_income REAL,
    ebitda REAL,
    total_assets REAL,
    equity REAL,
    net_debt REAL,
    shares_outstanding REAL,
    net_income_ttm REAL
);
"""

INDEX_FUNDAMENTALS_PIT_US_CIK = (
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_us_cik ON fundamentals_pit_us(cik);"
)
INDEX_FUNDAMENTALS_PIT_US_TICKER = (
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_us_ticker"
    " ON fundamentals_pit_us(ticker);"
)
INDEX_FUNDAMENTALS_PIT_US_FILING_DATE = (
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_us_filing_date"
    " ON fundamentals_pit_us(filing_date);"
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
    SCHEMA_FUNDAMENTALS_PIT_US,
    INDEX_FUNDAMENTALS_PIT_US_CIK,
    INDEX_FUNDAMENTALS_PIT_US_TICKER,
    INDEX_FUNDAMENTALS_PIT_US_FILING_DATE,
]

def _upsert_frame(conn, table: str, df: pd.DataFrame, key: list[str],
                  cols: list[str], stage_coltypes: str, stage_name: str) -> int:
    """Upsert `df[cols]` into `table` on `key`, on either backend.

    Factored out because the three multi-market tables want exactly the pattern
    upsert_prices already uses — COPY into a temp table then one INSERT ...
    ON CONFLICT on Postgres, executemany on SQLite — and repeating it three more
    times is how the two paths drift apart. The `ord`/DISTINCT ON dedup and the
    IS DISTINCT FROM guard carry over for the same reasons documented there: a
    batch may legally carry the same key twice (last wins), and re-ingesting an
    unchanged period should touch no rows.
    """
    from . import storage

    if df.empty:
        return 0

    value_cols = [c for c in cols if c not in key]
    placeholders = ", ".join("?" for _ in cols)
    updates_sqlite = ", ".join(f"{c} = excluded.{c}" for c in value_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "      # noqa: S608
        f"ON CONFLICT({', '.join(key)}) DO UPDATE SET {updates_sqlite}"
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
        updates = ", ".join(f"{c} = excluded.{c}" for c in value_cols)
        unchanged = " OR ".join(
            f"{table}.{c} IS DISTINCT FROM excluded.{c}" for c in value_cols)
        keylist = ", ".join(key)
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


def upsert_instruments(conn, df: pd.DataFrame) -> int:
    """Upsert rows into the security master. Missing optional columns become NULL."""
    if df.empty:
        logger.warning("No instrument records to upsert")
        return 0
    df = df.copy()
    for col in INSTRUMENT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in ("listing_date", "delisting_date", "first_seen", "last_seen"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ("is_worthless", "is_test_issue"):
        df[col] = df[col].fillna(0).astype(int)
    n = _upsert_frame(
        conn, "instruments", df, ["market", "symbol"], INSTRUMENT_COLUMNS,
        _INSTRUMENTS_STAGE_TYPES, "_instr_stage",
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


FUNDAMENTALS_PIT_US_COLUMNS = [
    "fact_id", "cik", "ticker", "period_end", "filing_date", "accn", "form",
    "fiscal_year", "fiscal_period", "revenue", "net_income", "ebitda",
    "total_assets", "equity", "net_debt", "shares_outstanding", "net_income_ttm",
]

_FUNDAMENTALS_PIT_US_STAGE_TYPES = (
    "fact_id TEXT, cik TEXT, ticker TEXT, period_end DATE, filing_date DATE,"
    " accn TEXT, form TEXT, fiscal_year BIGINT, fiscal_period TEXT,"
    " revenue DOUBLE PRECISION, net_income DOUBLE PRECISION,"
    " ebitda DOUBLE PRECISION, total_assets DOUBLE PRECISION,"
    " equity DOUBLE PRECISION, net_debt DOUBLE PRECISION,"
    " shares_outstanding DOUBLE PRECISION, net_income_ttm DOUBLE PRECISION"
)


def upsert_fundamentals_pit_us(conn, df: pd.DataFrame) -> int:
    """Upsert SEC-derived point-in-time fundamentals.

    Rows are never merged across accessions: a restatement arrives as its own
    fact_id with its own (later) filing_date, so a backtest dated before that
    date still reads the number the market actually had. That is the whole point
    of the table — see the note above SCHEMA_FUNDAMENTALS_PIT_US.
    """
    if df.empty:
        logger.warning("No US fundamentals records to upsert")
        return 0
    df = df.copy()
    for col in FUNDAMENTALS_PIT_US_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in ("period_end", "filing_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["fact_id", "cik", "period_end", "filing_date"])

    n = _upsert_frame(
        conn, "fundamentals_pit_us", df, ["fact_id"], FUNDAMENTALS_PIT_US_COLUMNS,
        _FUNDAMENTALS_PIT_US_STAGE_TYPES, "_fus_stage",
    )
    logger.info(f"Upserted {n:,} US fundamentals records")
    return n
