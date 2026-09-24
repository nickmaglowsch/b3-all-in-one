"""
Storage operations for B3 market data (SQLite by default, Postgres via
ENGINE_DATABASE_URL). The dialect map lives in `_xlate`/`_pg_ddl` below.
"""

import logging
import os
import re
import sqlite3
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from . import config, market_storage, split_storage, sql_stream, us_storage

logger = logging.getLogger(__name__)


# ── Dialect resolver + helpers ─────────────────────────────────────────────────
# ENGINE_DATABASE_URL selects the backend. Unset (or a sqlite path/URL) keeps the
# historical SQLite behaviour byte-for-byte; a postgresql:// DSN routes every
# connection through psycopg (v3).

_PG_PREFIXES = ("postgresql://", "postgres://")


def _is_pg_url(s) -> bool:
    return isinstance(s, str) and s.startswith(_PG_PREFIXES)


def using_postgres() -> bool:
    """True when ENGINE_DATABASE_URL points at Postgres (the global switch)."""
    return _is_pg_url(os.environ.get("ENGINE_DATABASE_URL"))


def _sqlite_path(url: str):
    """Strip a sqlite:// prefix if present; otherwise treat the value as a path."""
    for pre in ("sqlite:///", "sqlite://"):
        if url.startswith(pre):
            return url[len(pre):]
    return url


def _connect_postgres(dsn: str):
    import psycopg  # lazy: only imported in Postgres mode
    return psycopg.connect(dsn)


def is_postgres(conn) -> bool:
    """True when conn is a Postgres (psycopg) connection rather than sqlite3."""
    return not isinstance(conn, sqlite3.Connection)


def _xlate(conn, sql: str, params=None) -> str:
    """Translate sqlite ?/:name placeholders to psycopg pyformat when needed."""
    if not is_postgres(conn):
        return sql
    # psycopg scans for placeholders only when given params, and only unescapes
    # then — so a literal % (CRYPTO's `LIKE '%USDT'`, read as the placeholder
    # %U) is doubled exactly then, and before the rewrites that add % of their own.
    if params is not None:
        sql = sql.replace("%", "%%")                         # literal % -> %%
    sql = re.sub(r"(?<!:):([a-zA-Z_]\w*)", r"%(\1)s", sql)  # :name -> %(name)s
    return sql.replace("?", "%s")                            # ?     -> %s


def read_sql(sql: str, conn, params=None, **kwargs) -> pd.DataFrame:
    """DataFrame for one query, dialect-aware, batched either way (sql_stream has
    the why). A pandas-kwargs caller still gets pandas' own post-processing."""
    sql = _xlate(conn, sql, params)
    if kwargs:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # pandas warns on non-sqlite DBAPI conns
            return pd.read_sql_query(sql, conn, params=params, **kwargs)
    return sql_stream.read_dataframe(conn, sql, params, postgres=is_postgres(conn))


def execute(conn, sql: str, params=None):
    """Execute one statement, translating placeholders. Returns the cursor."""
    cur = conn.cursor()
    args = () if params is None else (params,)
    cur.execute(_xlate(conn, sql, params), *args)
    return cur


def executemany(conn, sql: str, rows) -> int:
    """Batch execute; sqlite3/psycopg only expose executemany on the cursor."""
    cur = conn.cursor()
    cur.executemany(_xlate(conn, sql, rows), rows)
    return len(rows)


def _copy_into_temp(conn, table: str, columns: list[str], coltypes: str, rows) -> None:
    """COPY `rows` into a fresh temp table, for a set-based statement to join to.

    One statement per row is what makes the price writes linear in the size of
    the whole table; COPY streams the batch in one round trip and lets the
    server do the rest in a single pass. Postgres only — callers keep
    executemany for SQLite, where it is already a prepared-statement loop inside
    one transaction.
    """
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TEMP TABLE {table} ({coltypes}) ON COMMIT DROP")
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _pg_ddl(sql: str) -> str:
    """Rewrite the SQLite CREATE statements to valid Postgres DDL.

    - REAL -> DOUBLE PRECISION (SQLite REAL is 8-byte; PG REAL is only 4-byte)
    - INTEGER -> BIGINT (SQLite INTEGER holds 8-byte ints)
    - drop FOREIGN KEY clauses: SQLite leaves them unenforced by default, so
      dropping them keeps behaviour identical while avoiding PG's enforcement.
    """
    sql = re.sub(
        r",(\s*--[^\n]*)*\s*FOREIGN KEY\s*\([^)]*\)\s*REFERENCES\s+\w+\s*\([^)]*\)",
        "",
        sql,
    )
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    sql = re.sub(r"\bINTEGER\b", "BIGINT", sql)
    return sql


def df_records(df: pd.DataFrame) -> list:
    """dict records with NaN/NaT normalised to None (SQL NULL) for both backends."""
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict("records")


SCHEMA_PRICES = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    isin_code TEXT NOT NULL,
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    -- Financial volume in BRL, not a share count: COTAHIST's VOLTOT carries two
    -- implied decimals and parser._parse_price divides by 100, so this is
    -- reais-and-centavos. Declared INTEGER until now, which _pg_ddl widened to
    -- BIGINT, so Postgres rounded every volume while SQLite's dynamic typing
    -- kept it — one pipeline storing two different numbers depending on the
    -- backend. external_prices.volume, the same quantity, was already REAL.
    volume REAL,
    quotation_factor INTEGER DEFAULT 1,
    split_adj_open REAL,
    split_adj_high REAL,
    split_adj_low REAL,
    split_adj_close REAL,
    adj_close REAL,
    PRIMARY KEY (ticker, date)
);
"""

SCHEMA_CORPORATE_ACTIONS = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    isin_code TEXT NOT NULL,
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL,
    value REAL,
    factor REAL,
    source TEXT DEFAULT 'B3',
    PRIMARY KEY (isin_code, event_date, event_type)
);
"""

SCHEMA_STOCK_ACTIONS = """
CREATE TABLE IF NOT EXISTS stock_actions (
    isin_code TEXT NOT NULL,
    ex_date DATE NOT NULL,
    action_type TEXT NOT NULL,
    factor REAL NOT NULL,
    source TEXT DEFAULT 'B3',
    PRIMARY KEY (isin_code, ex_date, action_type)
);
"""

SCHEMA_DETECTED_SPLITS = """
CREATE TABLE IF NOT EXISTS detected_splits (
    isin_code TEXT NOT NULL,
    ex_date DATE NOT NULL,
    split_factor REAL NOT NULL,
    description TEXT,
    PRIMARY KEY (isin_code, ex_date)
);
"""

SCHEMA_SKIPPED_EVENTS = """
CREATE TABLE IF NOT EXISTS skipped_events (
    isin_code TEXT NOT NULL,
    event_date DATE NOT NULL,
    label TEXT NOT NULL,
    factor REAL,
    source TEXT DEFAULT 'B3',
    reason TEXT,
    PRIMARY KEY (isin_code, event_date, label)
);
"""

SCHEMA_FETCH_FAILURES = """
CREATE TABLE IF NOT EXISTS fetch_failures (
    company_code TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    error_message TEXT,
    failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    resolved INTEGER DEFAULT 0,
    PRIMARY KEY (company_code, endpoint)
);
"""

INDEX_PRICES_DATE = "CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);"
INDEX_PRICES_ISIN = "CREATE INDEX IF NOT EXISTS idx_prices_isin ON prices(isin_code);"
# `prices` is PRIMARY KEY (ticker, date), whose leading column already serves
# every ticker-first access: an equality probe, a range within one ticker, an
# ORDER BY ticker. A separate index on (ticker) answered nothing the PK could
# not, and every bulk write into the biggest table here paid to maintain it.
# Dropped rather than merely un-created, so databases that ran the old schema
# stop carrying it too.
DROP_INDEX_PRICES_TICKER = "DROP INDEX IF EXISTS idx_prices_ticker;"

# Marks for instruments COTAHIST does not carry (see external_prices.py). A sibling
# of `prices` rather than rows inside it, for three reasons, each of which bites:
#   1. main.py step 6 derives corporate-action roots as `t[:4]` over every ticker
#      in `prices`. LFTB11 would add the root LFTB, which is no listed company —
#      it 404s into fetch_failures and gets retried on every run, forever.
#   2. main.py step 7 infers splits from price ratios across the whole table. That
#      step reads COTAHIST semantics; a series from another feed is outside its
#      contract even where it would not fire today.
#   3. init_db(rebuild=True) drops `prices`. COTAHIST can be re-downloaded, so that
#      is merely slow. This table cannot: B3's daily file has a ~14-month rolling
#      window, so a dropped row older than that is gone for good. It is therefore
#      absent from the rebuild drop list on purpose.
# A `prices.source` column would fix (3) alone, at the price of making every
# existing reader — backtests, adjustments, split detection, /price-history —
# filter on it. One writer's need should not tax every reader.
SCHEMA_EXTERNAL_PRICES = """
CREATE TABLE IF NOT EXISTS external_prices (
    ticker TEXT NOT NULL,
    isin_code TEXT NOT NULL,
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    source TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);
"""
INDEX_EXTERNAL_PRICES_DATE = (
    "CREATE INDEX IF NOT EXISTS idx_external_prices_date ON external_prices(date);"
)

# Days a feed above owed and could not fetch. Separate from `fetch_failures`,
# which keys on (company_code, endpoint) and holds B3 company-API 404s: that
# table's rows are mostly permanent and expected, and mixing a "this company has
# no listed page" with a "this session is about to fall off a 14-month window"
# would bury the second in the first — they differ in urgency, in retry policy
# and in what a non-empty count means. Two questions, two tables.
#
# Exempt from the rebuild drop for the same reason `external_prices` is: a
# pending retry is a claim on rows that cannot be re-fetched once B3's window
# passes, so dropping the ledger loses that data one step earlier than dropping
# the prices would.
SCHEMA_EXTERNAL_PRICE_FAILURES = """
CREATE TABLE IF NOT EXISTS external_price_failures (
    source TEXT NOT NULL,
    date DATE NOT NULL,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, date)
);
"""

# ── CVM Fundamentals Tables ────────────────────────────────────────────────────

SCHEMA_CVM_COMPANIES = """
CREATE TABLE IF NOT EXISTS cvm_companies (
    cnpj TEXT NOT NULL PRIMARY KEY,
    ticker TEXT,
    ticker_root TEXT,
    company_name TEXT,
    cvm_code TEXT,
    b3_trading_name TEXT,
    listing_date DATE,
    delisting_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_CVM_FILINGS = """
CREATE TABLE IF NOT EXISTS cvm_filings (
    filing_id TEXT NOT NULL PRIMARY KEY,
    cnpj TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    period_end DATE NOT NULL,
    filing_date DATE NOT NULL,
    filing_version INTEGER NOT NULL DEFAULT 1,
    fiscal_year INTEGER,
    quarter INTEGER,
    source_file TEXT
);
"""

SCHEMA_FUNDAMENTALS_PIT = """
CREATE TABLE IF NOT EXISTS fundamentals_pit (
    filing_id TEXT NOT NULL PRIMARY KEY,
    cnpj TEXT NOT NULL,
    ticker TEXT,
    period_end DATE NOT NULL,
    filing_date DATE NOT NULL,
    filing_version INTEGER NOT NULL DEFAULT 1,
    doc_type TEXT NOT NULL,
    fiscal_year INTEGER,
    quarter INTEGER,
    revenue REAL,
    net_income REAL,
    ebitda REAL,
    total_assets REAL,
    equity REAL,
    net_debt REAL,
    shares_outstanding REAL,
    -- Per-class share counts from FRE capital_social (ON / all preferred
    -- classes). NULL for pre-FRE sources (IAN/DM).
    shares_on REAL,
    shares_pn REAL,
    -- Trailing-twelve-months net income, computed post-parse (NULL when the
    -- prior-year values needed for the TTM sum are missing).
    net_income_ttm REAL,
    -- Ratio columns (pe_ratio, pb_ratio, ev_ebitda) intentionally excluded — compute dynamically at query time
    FOREIGN KEY (filing_id) REFERENCES cvm_filings(filing_id)
);
"""

# Note: all financial values are stored in thousands of BRL as reported by CVM.
# Do NOT multiply by 1000 when storing or reading.

SCHEMA_COMPANY_ISIN_MAP = """
CREATE TABLE IF NOT EXISTS company_isin_map (
    cnpj TEXT NOT NULL,
    isin_code TEXT NOT NULL,
    ticker TEXT NOT NULL,
    share_class TEXT,
    is_primary INTEGER DEFAULT 0,
    first_seen DATE,
    last_seen DATE,
    PRIMARY KEY (cnpj, isin_code)
);
"""

INDEX_COMPANY_ISIN_MAP_CNPJ = "CREATE INDEX IF NOT EXISTS idx_company_isin_map_cnpj ON company_isin_map(cnpj);"
INDEX_COMPANY_ISIN_MAP_ISIN = "CREATE INDEX IF NOT EXISTS idx_company_isin_map_isin ON company_isin_map(isin_code);"
INDEX_COMPANY_ISIN_MAP_TICKER = "CREATE INDEX IF NOT EXISTS idx_company_isin_map_ticker ON company_isin_map(ticker);"
INDEX_COMPANY_ISIN_MAP_PRIMARY = "CREATE INDEX IF NOT EXISTS idx_company_isin_map_primary ON company_isin_map(cnpj, is_primary);"

SCHEMA_FUNDAMENTALS_MONTHLY = """
CREATE TABLE IF NOT EXISTS fundamentals_monthly (
    month_end DATE NOT NULL,
    ticker TEXT NOT NULL,
    revenue REAL,
    net_income REAL,
    ebitda REAL,
    total_assets REAL,
    equity REAL,
    net_debt REAL,
    shares_outstanding REAL,
    net_income_ttm REAL,
    -- Ratio columns (pe_ratio, pb_ratio, ev_ebitda) intentionally excluded — compute dynamically at query time
    PRIMARY KEY (month_end, ticker)
);
"""

# Point-in-time CNPJ -> ticker mapping from the CVM FCA valor_mobiliario dataset.
# Unlike the B3 API (currently listed companies only), FCA covers companies that
# have since delisted — closing the survivorship hole in ticker mapping.
SCHEMA_COMPANY_TICKERS_PIT = """
CREATE TABLE IF NOT EXISTS company_tickers_pit (
    cnpj TEXT NOT NULL,
    ticker TEXT NOT NULL,
    ticker_root TEXT,
    market TEXT,
    start_date DATE,
    end_date DATE,
    source TEXT DEFAULT 'FCA',
    PRIMARY KEY (cnpj, ticker, start_date)
);
"""

INDEX_COMPANY_TICKERS_PIT_ROOT = "CREATE INDEX IF NOT EXISTS idx_company_tickers_pit_root ON company_tickers_pit(ticker_root);"

INDEX_FUNDAMENTALS_MONTHLY_TICKER = "CREATE INDEX IF NOT EXISTS idx_fundamentals_monthly_ticker ON fundamentals_monthly(ticker);"
INDEX_FUNDAMENTALS_MONTHLY_MONTH_END = "CREATE INDEX IF NOT EXISTS idx_fundamentals_monthly_month_end ON fundamentals_monthly(month_end);"

INDEX_CVM_COMPANIES_TICKER = "CREATE INDEX IF NOT EXISTS idx_cvm_companies_ticker ON cvm_companies(ticker);"
INDEX_CVM_COMPANIES_TICKER_ROOT = "CREATE INDEX IF NOT EXISTS idx_cvm_companies_ticker_root ON cvm_companies(ticker_root);"
INDEX_CVM_FILINGS_CNPJ = "CREATE INDEX IF NOT EXISTS idx_cvm_filings_cnpj ON cvm_filings(cnpj);"
INDEX_CVM_FILINGS_PERIOD = "CREATE INDEX IF NOT EXISTS idx_cvm_filings_period ON cvm_filings(period_end);"
INDEX_FUNDAMENTALS_PIT_CNPJ = "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_cnpj ON fundamentals_pit(cnpj);"
INDEX_FUNDAMENTALS_PIT_TICKER = "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_ticker ON fundamentals_pit(ticker);"
INDEX_FUNDAMENTALS_PIT_FILING_DATE = "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_filing_date ON fundamentals_pit(filing_date);"
INDEX_FUNDAMENTALS_PIT_PERIOD = "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_period ON fundamentals_pit(period_end);"


def describe_target(db_path: Optional[Path] = None) -> str:
    """Human-readable description of where get_connection() will write, with any
    password masked. For logs — a run that reports a SQLite path while writing to
    Postgres is a bad first clue when a scheduled ingest doesn't move the plane.
    Mirrors the precedence in get_connection below.
    """
    env = os.environ.get("ENGINE_DATABASE_URL")
    dsn = env if _is_pg_url(env) else (str(db_path) if _is_pg_url(db_path) else None)
    if dsn is None:
        if db_path is not None:
            return f"sqlite:{db_path}"
        return f"sqlite:{_sqlite_path(env) if env else config.DB_PATH}"
    scheme, _, rest = dsn.partition("://")
    creds, at, host = rest.rpartition("@")
    if not at:
        return dsn
    return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"


def get_connection(db_path: Optional[Path] = None):
    """Return a DB connection, resolving the backend from ENGINE_DATABASE_URL.

    Precedence (the default keeps today's SQLite behaviour exactly):
      1. ENGINE_DATABASE_URL=postgresql://...  -> Postgres (global switch)
      2. db_path is a postgresql:// DSN         -> Postgres
      3. db_path given                          -> SQLite at that path (unchanged)
      4. ENGINE_DATABASE_URL is a sqlite path   -> SQLite there
      5. neither                                -> SQLite at config.DB_PATH
    """
    env = os.environ.get("ENGINE_DATABASE_URL")
    if _is_pg_url(env):
        return _connect_postgres(env)
    if _is_pg_url(db_path):
        return _connect_postgres(str(db_path))
    if db_path is None and env:
        db_path = _sqlite_path(env)
    if db_path is None:
        db_path = config.DB_PATH

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    return conn


# The rebuild drop sets, in the historical order: CVM first, then COTAHIST. The
# cvm_only rebuild drops the first tuple alone — that is the whole difference.
_CVM_TABLES = ("fundamentals_monthly", "fundamentals_pit", "cvm_filings",
               "company_isin_map", "company_tickers_pit", "cvm_companies")
_COTAHIST_TABLES = ("prices", "corporate_actions", "stock_actions",
                    "detected_splits", "skipped_events", "fetch_failures")


def init_db(conn: sqlite3.Connection, rebuild: bool = False, cvm_only: bool = False) -> None:
    """Initialize the database schema.

    Args:
        conn: SQLite connection.
        rebuild: When True, drop tables before recreating them.
        cvm_only: When True (and rebuild=True), drop only _CVM_TABLES and leave
            _COTAHIST_TABLES intact. When False (default), both sets go — the
            full-pipeline rebuild behaviour used by main.py.
    """
    cursor = conn.cursor()

    if rebuild:
        if cvm_only:
            logger.info("Dropping CVM tables only (prices and COTAHIST tables preserved)...")
            drop = _CVM_TABLES
        else:
            logger.warning(
                "Full rebuild: dropping ALL tables including prices and corporate actions"
            )
            drop = _CVM_TABLES + _COTAHIST_TABLES
        for table in drop:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608

    logger.info("Creating database schema...")
    pg = is_postgres(conn)
    # Order matches the historical create sequence exactly. INDEX_CVM_COMPANIES_
    # TICKER_ROOT is intentionally NOT here: on an old SQLite file its column is
    # only added by _migrate_schema below, so the index has to come after that.
    ddl_statements = [
        SCHEMA_PRICES,
        SCHEMA_CORPORATE_ACTIONS,
        SCHEMA_STOCK_ACTIONS,
        SCHEMA_DETECTED_SPLITS,
        SCHEMA_SKIPPED_EVENTS,
        SCHEMA_FETCH_FAILURES,
        INDEX_PRICES_DATE,
        INDEX_PRICES_ISIN,
        DROP_INDEX_PRICES_TICKER,
        SCHEMA_EXTERNAL_PRICES,
        INDEX_EXTERNAL_PRICES_DATE,
        SCHEMA_EXTERNAL_PRICE_FAILURES,
        SCHEMA_CVM_COMPANIES,
        SCHEMA_CVM_FILINGS,
        SCHEMA_FUNDAMENTALS_PIT,
        SCHEMA_COMPANY_ISIN_MAP,
        SCHEMA_COMPANY_TICKERS_PIT,
        INDEX_COMPANY_TICKERS_PIT_ROOT,
        INDEX_CVM_COMPANIES_TICKER,
        INDEX_CVM_FILINGS_CNPJ,
        INDEX_CVM_FILINGS_PERIOD,
        INDEX_FUNDAMENTALS_PIT_CNPJ,
        INDEX_FUNDAMENTALS_PIT_TICKER,
        INDEX_FUNDAMENTALS_PIT_FILING_DATE,
        INDEX_FUNDAMENTALS_PIT_PERIOD,
        INDEX_COMPANY_ISIN_MAP_CNPJ,
        INDEX_COMPANY_ISIN_MAP_ISIN,
        INDEX_COMPANY_ISIN_MAP_TICKER,
        INDEX_COMPANY_ISIN_MAP_PRIMARY,
        SCHEMA_FUNDAMENTALS_MONTHLY,
        INDEX_FUNDAMENTALS_MONTHLY_TICKER,
        INDEX_FUNDAMENTALS_MONTHLY_MONTH_END,
    ] + market_storage.DDL + split_storage.DDL + us_storage.DDL
    for stmt in ddl_statements:
        cursor.execute(_pg_ddl(stmt) if pg else stmt)

    if pg:
        # Postgres additive migrations (mirrors _migrate_schema, native IF NOT EXISTS).
        _migrate_schema_pg(conn)
    else:
        # Migrate existing SQLite databases (add columns to pre-existing files).
        # PRAGMA/ALTER used here don't exist in Postgres.
        _migrate_schema(conn)

    conn.commit()
    logger.info("Database schema initialized")


# Columns added after their table first shipped: the full CREATEs above already
# carry them, so these only bite a database created from an older engine schema.
# (table, column, SQLite type) — the Postgres type is the same _pg_ddl translation.
_ADDED_COLUMNS = (
    ("prices", "quotation_factor", "INTEGER DEFAULT 1"),
    ("cvm_companies", "listing_date", "DATE"),
    ("cvm_companies", "delisting_date", "DATE"),
    ("cvm_companies", "ticker_root", "TEXT"),
    ("fundamentals_pit", "net_income_ttm", "REAL"),
    ("fundamentals_monthly", "net_income_ttm", "REAL"),
    ("fundamentals_pit", "shares_on", "REAL"),
    ("fundamentals_pit", "shares_pn", "REAL"),
)


def _migrate_schema_pg(conn) -> None:
    """Postgres equivalent of _migrate_schema: additive, idempotent column adds.

    A fresh PG database already has every column (the full CREATE above ran
    through _pg_ddl), so this is a no-op there. It only bites a PG database
    created from an older engine schema — the same case _migrate_schema covers on
    SQLite. Uses PG's native ADD COLUMN IF NOT EXISTS, no information_schema
    probing, no migration framework. Types match the _pg_ddl-translated CREATE
    (REAL -> DOUBLE PRECISION, INTEGER -> BIGINT).
    """
    cursor = conn.cursor()
    for table, col, col_type in _ADDED_COLUMNS:
        cursor.execute(
            _pg_ddl(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))

    # Widen prices.volume from the BIGINT an older schema created. Unlike the
    # additive statements above this is a rewriting ALTER that takes an ACCESS
    # EXCLUSIVE lock on the biggest table here, and init_db runs at the start of
    # every pipeline run — so it is probed first and issued only when it would
    # actually change something.
    #
    # Values already stored were rounded on the way in; widening the column
    # cannot recover the centavos. They come back on the next re-ingest of the
    # affected years, which the pipeline does anyway for the current year.
    cursor.execute(
        "SELECT data_type FROM information_schema.columns"
        " WHERE table_name = 'prices' AND column_name = 'volume'"
        "   AND table_schema = current_schema()"
    )
    row = cursor.fetchone()
    if row and row[0] != "double precision":
        logger.info("Migrating: widening prices.volume to DOUBLE PRECISION")
        execute(conn, "ALTER TABLE prices ALTER COLUMN volume TYPE DOUBLE PRECISION")

    cursor.execute(INDEX_CVM_COMPANIES_TICKER_ROOT)
    # Backfill ticker_root like the SQLite path (idempotent: only fills NULLs).
    cursor.execute(
        "UPDATE cvm_companies SET ticker_root = SUBSTR(ticker, 1, 4)"
        " WHERE ticker IS NOT NULL AND ticker_root IS NULL"
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply non-destructive schema migrations for existing databases."""
    cursor = conn.cursor()
    added = set()
    for table, col, col_type in _ADDED_COLUMNS:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cursor.fetchall()}
        if cols and col not in cols:
            logger.info(f"Migrating: adding {col} column to {table}")
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            added.add(col)

    if "ticker_root" in added:
        cursor.execute(
            "UPDATE cvm_companies SET ticker_root = SUBSTR(ticker, 1, 4)"
            " WHERE ticker IS NOT NULL AND ticker_root IS NULL"
        )
    # Ensure ticker_root index exists for both fresh and migrated DBs.
    cursor.execute(INDEX_CVM_COMPANIES_TICKER_ROOT)


def upsert_prices(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert price records into the database."""
    if df.empty:
        logger.warning("No price records to upsert")
        return 0

    sql = """
        INSERT INTO prices (ticker, isin_code, date, open, high, low, close, volume, quotation_factor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            isin_code = excluded.isin_code,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            quotation_factor = excluded.quotation_factor
    """

    df = df.copy()
    if "isin_code" not in df.columns:
        df["isin_code"] = "UNKNOWN"
    else:
        df["isin_code"] = df["isin_code"].fillna("UNKNOWN")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    if "quotation_factor" not in df.columns:
        df["quotation_factor"] = 1
    else:
        df["quotation_factor"] = df["quotation_factor"].fillna(1).astype(int)

    # Drop records with non-positive prices or zero volume — these are non-trading
    # entries or B3 data errors that would corrupt backtests.
    before = len(df)
    df = df[(df["close"] > 0) & (df["volume"] > 0)]
    dropped_zero = before - len(df)
    if dropped_zero:
        logger.warning(f"Dropped {dropped_zero:,} rows with zero/negative close or zero volume")

    # Drop records where OHLC is internally inconsistent (high < low, or close
    # outside [low, high]).  These are data quality errors in the source files,
    # concentrated in pre-2010 COTAHIST data.
    before = len(df)
    valid_ohlc = (df["high"] >= df["low"]) & (df["close"] >= df["low"]) & (df["close"] <= df["high"])
    df = df[valid_ohlc]
    dropped_ohlc = before - len(df)
    if dropped_ohlc:
        logger.warning(f"Dropped {dropped_ohlc:,} rows with invalid OHLC (close outside [low, high] or high < low)")

    cols = ["ticker", "isin_code", "date", "open", "high", "low", "close", "volume", "quotation_factor"]
    records = df[cols].values.tolist()

    if is_postgres(conn):
        # Stage with COPY, then one INSERT ... ON CONFLICT. executemany issued a
        # statement per row into the largest table here: 4.3s for 302k rows,
        # linear in the whole price history.
        #
        # The `ord` column and DISTINCT ON reproduce what executemany did when a
        # batch carried the same (ticker, date) twice — it applied them in order,
        # so the last one won. Without the dedup a single ON CONFLICT statement
        # raises "cannot affect row a second time".
        _copy_into_temp(
            conn, "_px_stage",
            [*cols, "ord"],
            "ticker TEXT, isin_code TEXT, date DATE, open DOUBLE PRECISION,"
            " high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,"
            " volume DOUBLE PRECISION, quotation_factor INTEGER, ord BIGINT",
            ([*r, i] for i, r in enumerate(records)),
        )
        collist = ", ".join(cols)
        value_cols = [c for c in cols if c not in ("ticker", "date")]
        updates = ", ".join(f"{c} = excluded.{c}" for c in value_cols)
        # Re-ingesting a year that has not changed should touch nothing; without
        # this every conflicting row is rewritten to its own current value.
        unchanged = " OR ".join(
            f"prices.{c} IS DISTINCT FROM excluded.{c}" for c in value_cols)
        execute(conn, f"""
            INSERT INTO prices ({collist})
            SELECT {collist} FROM (
                SELECT DISTINCT ON (ticker, date) {collist}
                FROM _px_stage ORDER BY ticker, date, ord DESC
            ) deduped
            ON CONFLICT (ticker, date) DO UPDATE SET {updates}
            WHERE ({unchanged})
        """)
    else:
        executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} price records")
    return len(records)


def _upsert_sql(table: str, cols: list, key: list) -> str:
    """INSERT … ON CONFLICT(key) DO UPDATE of every non-key column."""
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key)
    return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
            f" ON CONFLICT({', '.join(key)}) DO UPDATE SET {updates}")


def upsert_external_prices(conn, df: pd.DataFrame) -> int:
    """Upsert marks from a non-COTAHIST feed into `external_prices`.

    Deliberately does not go through upsert_prices: that one drops rows with zero
    volume and enforces OHLC containment, both of which assume COTAHIST's fields.
    This feed publishes no opening price at all, so `open` is NULL by design and a
    close-outside-[low,high] check is all the validation the shape supports.
    """
    if df.empty:
        logger.warning("No external price records to upsert")
        return 0

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    before = len(df)
    df = df[df["close"] > 0]
    bad = df["high"].notna() & df["low"].notna() & (
        (df["high"] < df["low"]) | (df["close"] < df["low"]) | (df["close"] > df["high"]))
    df = df[~bad]
    if before - len(df):
        logger.warning(f"Dropped {before - len(df):,} external rows with bad prices")

    cols = ["ticker", "isin_code", "date", "open", "high", "low", "close", "volume", "source"]
    records = df[cols].astype(object).where(pd.notna(df[cols]), None).values.tolist()
    executemany(conn, _upsert_sql("external_prices", cols, ["ticker", "date"]), records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} external price records")
    return len(records)


def _as_date(value) -> date:
    """Postgres hands back a date, SQLite a string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# Both helpers below answer "what has this feed already got?", and both are used
# to decide what NOT to fetch — so they must be scoped to one feed. Unscoped,
# any second writer to the shared table (the BCB index series, a Tesouro Direto
# loader) makes the ETF backfill believe those dates are covered and skip them.
# B3's window is ~14 months and rolling, so a skipped day is gone for good.
def max_external_price_date(conn, source: str) -> Optional[date]:
    """Latest date already stored for `source` — None when it has no rows."""
    row = execute(
        conn, "SELECT MAX(date) FROM external_prices WHERE source = ?",
        (source,),
    ).fetchone()
    return _as_date(row[0]) if row and row[0] else None


def stored_external_dates(conn, start: date, source: str) -> set:
    """Dates from `start` that `source` already has rows for.

    The caller fetches the complement rather than everything after the latest
    stored date. Resuming from the max would step over a day a transient network
    error skipped, and with a source on a rolling retention window a skipped day
    becomes permanently unrecoverable — the one failure mode worth extra code.
    """
    rows = execute(
        conn,
        "SELECT DISTINCT date FROM external_prices "
        "WHERE date >= ? AND source = ?",
        (start.isoformat(), source),
    ).fetchall()
    return {_as_date(r[0]) for r in rows if r and r[0]}


def record_external_failure(conn, source: str, day: date, error: str) -> None:
    """Remember that `source` owes `day`, so a later run can pay it.

    Re-recording the same day bumps retry_count instead of duplicating: the count
    is what distinguishes one bad night from a feed that has been broken for a
    fortnight, and only the second is worth waking someone for.
    """
    execute(
        conn,
        """
        INSERT INTO external_price_failures (source, date, error, retry_count)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(source, date) DO UPDATE SET
            error = excluded.error,
            failed_at = CURRENT_TIMESTAMP,
            retry_count = external_price_failures.retry_count + 1
        """,
        (source, day.isoformat(), error[:500]),
    )
    conn.commit()


def clear_external_failure(conn, source: str, day: date) -> None:
    """Drop a day from the retry queue — it either arrived or is unrecoverable."""
    execute(
        conn,
        "DELETE FROM external_price_failures WHERE source = ? AND date = ?",
        (source, day.isoformat()),
    )
    conn.commit()


def pending_external_failures(conn, source: str) -> set:
    """Days `source` still owes. Scoped, like every other question of this table:
    `external_prices` has three writers and one feed's outage is not another's."""
    rows = execute(
        conn,
        "SELECT date FROM external_price_failures WHERE source = ?",
        (source,),
    ).fetchall()
    return {_as_date(r[0]) for r in rows if r and r[0]}


def _upsert_rows(conn, df: pd.DataFrame, table: str, cols: list, key: list,
                 noun: str, defaults: dict) -> int:
    """The B3 corporate-action tables: key[1] is the date, absent optional
    columns take `defaults`, and a missing `source` is B3's."""
    if df.empty:
        logger.warning(f"No {noun} records to upsert")
        return 0

    df = df.copy()
    df[key[1]] = pd.to_datetime(df[key[1]]).dt.strftime("%Y-%m-%d")
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
    if "source" in cols:
        df["source"] = df["source"].fillna("B3")

    records = df[cols].values.tolist()
    executemany(conn, _upsert_sql(table, cols, key), records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} {noun} records")
    return len(records)


def upsert_corporate_actions(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert corporate action records into the database."""
    return _upsert_rows(
        conn, df, "corporate_actions",
        ["isin_code", "event_date", "event_type", "value", "factor", "source"],
        ["isin_code", "event_date", "event_type"], "corporate action",
        {"value": None, "factor": None, "source": "B3"})


def upsert_detected_splits(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert detected split records into the database."""
    return _upsert_rows(
        conn, df, "detected_splits",
        ["isin_code", "ex_date", "split_factor", "description"],
        ["isin_code", "ex_date"], "detected split", {"description": None})


def upsert_stock_actions(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert stock action records (splits, reverse splits, bonuses) into the database."""
    return _upsert_rows(
        conn, df, "stock_actions",
        ["isin_code", "ex_date", "action_type", "factor", "source"],
        ["isin_code", "ex_date", "action_type"], "stock action", {"source": "B3"})


def replace_stock_actions(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Replace the full stock_actions table with a validated action set.

    Used after adjustment-time validation drops phantom rows / corrects factors,
    so all consumers (share-count adjustment, backtests) read the same truth.

    Delete + insert run in a single transaction (the commit inside
    upsert_stock_actions covers both), so a crash mid-way cannot leave the
    table permanently empty.
    """
    try:
        conn.execute("DELETE FROM stock_actions")
        if df.empty:
            logger.warning("replace_stock_actions: validated set is empty")
            conn.commit()
            return 0
        return upsert_stock_actions(conn, df)
    except Exception:
        conn.rollback()
        raise


def update_adjusted_columns(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Update adjusted columns for existing price records."""
    if df.empty:
        return 0

    sql = """
        UPDATE prices
        SET split_adj_open = ?,
            split_adj_high = ?,
            split_adj_low = ?,
            split_adj_close = ?,
            adj_close = ?,
            volume = ?
        WHERE ticker = ? AND isin_code = ? AND date = ?
    """

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    if "isin_code" not in df.columns:
        df["isin_code"] = "UNKNOWN"
    else:
        df["isin_code"] = df["isin_code"].fillna("UNKNOWN")
    for col in ["split_adj_open", "split_adj_high", "split_adj_low", "split_adj_close", "adj_close"]:
        if col not in df.columns:
            df[col] = None
    if "volume" not in df.columns:
        df["volume"] = None

    cols = ["split_adj_open", "split_adj_high", "split_adj_low", "split_adj_close", "adj_close", "volume", "ticker", "isin_code", "date"]
    records = df[cols].values.tolist()

    if is_postgres(conn):
        # Same story as upsert_prices, and worse: this one UPDATEs essentially
        # every row in the table on every pipeline run — 7.9s for 302k rows.
        # COPY the batch, then one UPDATE ... FROM.
        #
        # No dedup here: an UPDATE ... FROM with duplicate keys picks one matching
        # staged row non-deterministically, but the frame this receives is
        # adjusted prices keyed by (ticker, isin_code, date), which is unique by
        # construction — it comes straight from get_all_prices.
        _copy_into_temp(
            conn, "_adj_stage", cols,
            "split_adj_open DOUBLE PRECISION, split_adj_high DOUBLE PRECISION,"
            " split_adj_low DOUBLE PRECISION, split_adj_close DOUBLE PRECISION,"
            " adj_close DOUBLE PRECISION, volume DOUBLE PRECISION,"
            " ticker TEXT, isin_code TEXT, date DATE",
            records,
        )
        value_cols = [c for c in cols if c not in ("ticker", "isin_code", "date")]
        setters = ", ".join(f"{c} = s.{c}" for c in value_cols)
        # Skip rows whose adjusted values already match. A nightly run recomputes
        # adjustments for the whole history but only recent dates and tickers with
        # a new corporate action actually move; rewriting the rest costs a tuple
        # version, a WAL record and vacuum work for each, to store what is
        # already there. IS DISTINCT FROM so NULL vs NULL counts as unchanged.
        changed = " OR ".join(f"p.{c} IS DISTINCT FROM s.{c}" for c in value_cols)
        execute(conn, f"""
            UPDATE prices p SET {setters}
            FROM _adj_stage s
            WHERE p.ticker = s.ticker AND p.isin_code = s.isin_code AND p.date = s.date
              AND ({changed})
        """)
    else:
        executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Updated adjusted columns for {len(records):,} records")
    return len(records)


def get_all_tickers(conn: sqlite3.Connection) -> List[str]:
    """Get all unique ticker symbols from the database."""
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM prices ORDER BY ticker")
    return [row[0] for row in cursor.fetchall()]


def get_ticker_isin_map(conn: sqlite3.Connection) -> dict:
    """Get mapping of ticker to most recent ISIN code."""
    cursor = conn.cursor()
    # Find the most recent ISIN for each ticker
    cursor.execute("""
        SELECT p.ticker, p.isin_code
        FROM prices p
        INNER JOIN (
            SELECT ticker, MAX(date) as max_date
            FROM prices
            WHERE isin_code != 'UNKNOWN'
            GROUP BY ticker
        ) latest ON p.ticker = latest.ticker AND p.date = latest.max_date
        WHERE p.isin_code != 'UNKNOWN'
    """)
    return {row[0]: row[1] for row in cursor.fetchall()}




def get_all_prices(conn: sqlite3.Connection) -> pd.DataFrame:
    """Get all price records from the database."""
    query = """
        SELECT ticker, isin_code, date, open, high, low, close, volume,
               quotation_factor,
               split_adj_open, split_adj_high, split_adj_low, split_adj_close, adj_close
        FROM prices
        ORDER BY isin_code, date
    """
    return read_sql(query, conn)


def get_all_corporate_actions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Get all corporate action records from the database."""
    query = """
        SELECT isin_code, event_date, event_type, value, factor, source
        FROM corporate_actions
        ORDER BY isin_code, event_date
    """
    return read_sql(query, conn)


def get_all_stock_actions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Get all stock action records from the database."""
    query = """
        SELECT isin_code, ex_date, action_type, factor, source
        FROM stock_actions
        ORDER BY isin_code, ex_date
    """
    return read_sql(query, conn)


def upsert_skipped_events(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert skipped event records (unrecognized B3 labels) into the database."""
    if df.empty:
        return 0
    return _upsert_rows(
        conn, df, "skipped_events",
        ["isin_code", "event_date", "label", "factor", "source", "reason"],
        ["isin_code", "event_date", "label"], "skipped event",
        {"factor": None, "source": "B3", "reason": None})


def record_fetch_failure(
    conn: sqlite3.Connection,
    company_code: str,
    endpoint: str,
    error_message: str,
) -> None:
    """Record an API fetch failure, incrementing retry_count if already exists."""
    sql = """
        INSERT INTO fetch_failures (company_code, endpoint, error_message, retry_count, resolved)
        VALUES (?, ?, ?, 0, 0)
        ON CONFLICT(company_code, endpoint) DO UPDATE SET
            error_message = excluded.error_message,
            failed_at = CURRENT_TIMESTAMP,
            retry_count = fetch_failures.retry_count + 1,
            resolved = 0
    """
    execute(conn, sql, (company_code, endpoint, error_message))
    conn.commit()


def resolve_fetch_failure(
    conn: sqlite3.Connection, company_code: str, endpoint: str
) -> None:
    """Mark a fetch failure as resolved."""
    execute(
        conn,
        "UPDATE fetch_failures SET resolved = 1 WHERE company_code = ? AND endpoint = ?",
        (company_code, endpoint),
    )
    conn.commit()


def get_unresolved_failures(conn: sqlite3.Connection) -> list:
    """Return all unresolved fetch failures as a list of dicts."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT company_code, endpoint, error_message, failed_at, retry_count
        FROM fetch_failures
        WHERE resolved = 0
        ORDER BY company_code
        """
    )
    cols = ["company_code", "endpoint", "error_message", "failed_at", "retry_count"]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_summary_stats(conn: sqlite3.Connection) -> dict:
    """Get summary statistics from the database."""
    cursor = conn.cursor()

    def count(sql: str) -> int:
        cursor.execute(sql)
        return cursor.fetchone()[0]

    stats = {key: count(sql) for key, sql in (
        ("total_prices", "SELECT COUNT(*) FROM prices"),
        ("total_tickers", "SELECT COUNT(DISTINCT ticker) FROM prices"),
        ("total_isins", "SELECT COUNT(DISTINCT isin_code) FROM prices"),
    )}
    cursor.execute("SELECT MIN(date), MAX(date) FROM prices")
    date_range = cursor.fetchone()
    stats["date_range"] = (date_range[0], date_range[1])
    for key, sql in (
        ("total_corporate_actions", "SELECT COUNT(*) FROM corporate_actions"),
        ("total_detected_splits", "SELECT COUNT(*) FROM detected_splits"),
        ("total_stock_actions", "SELECT COUNT(*) FROM stock_actions"),
        ("total_skipped_events", "SELECT COUNT(*) FROM skipped_events"),
        ("unresolved_fetch_failures", "SELECT COUNT(*) FROM fetch_failures WHERE resolved = 0"),
    ):
        stats[key] = count(sql)

    # CVM fundamentals counts (tables may not exist in very old DBs — default to 0)
    for key, table in (("total_cvm_companies", "cvm_companies"),
                       ("total_cvm_filings", "cvm_filings"),
                       ("total_fundamentals_pit", "fundamentals_pit")):
        try:
            stats[key] = count(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        except Exception:
            stats[key] = 0
    return stats
