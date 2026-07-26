"""
Storage operations for B3 market data (SQLite by default, Postgres via
ENGINE_DATABASE_URL). See PORTING.md for the dialect map.
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

from . import config

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


def _xlate(conn, sql: str) -> str:
    """Translate sqlite ?/:name placeholders to psycopg pyformat when needed."""
    if not is_postgres(conn):
        return sql
    sql = re.sub(r"(?<!:):([a-zA-Z_]\w*)", r"%(\1)s", sql)  # :name -> %(name)s
    return sql.replace("?", "%s")                            # ?     -> %s


def read_sql(sql: str, conn, params=None, **kwargs) -> pd.DataFrame:
    """pd.read_sql_query with dialect-aware placeholders (both backends).

    Extra kwargs (e.g. parse_dates) are forwarded to pandas.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pandas warns on non-sqlite DBAPI conns
        return pd.read_sql_query(_xlate(conn, sql), conn, params=params, **kwargs)


def execute(conn, sql: str, params=None):
    """Execute one statement, translating placeholders. Returns the cursor."""
    cur = conn.cursor()
    if params is None:
        cur.execute(_xlate(conn, sql))
    else:
        cur.execute(_xlate(conn, sql), params)
    return cur


def executemany(conn, sql: str, rows) -> int:
    """Batch execute; sqlite3/psycopg only expose executemany on the cursor."""
    cur = conn.cursor()
    cur.executemany(_xlate(conn, sql), rows)
    return len(rows)


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
    volume INTEGER,
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
INDEX_PRICES_TICKER = "CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);"
INDEX_PRICES_ISIN = "CREATE INDEX IF NOT EXISTS idx_prices_isin ON prices(isin_code);"

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


def init_db(conn: sqlite3.Connection, rebuild: bool = False, cvm_only: bool = False) -> None:
    """Initialize the database schema.

    Args:
        conn: SQLite connection.
        rebuild: When True, drop tables before recreating them.
        cvm_only: When True (and rebuild=True), only drop the 5 CVM-specific tables
            (fundamentals_monthly, fundamentals_pit, cvm_filings, company_isin_map,
            cvm_companies).  COTAHIST-owned tables (prices, corporate_actions,
            stock_actions, detected_splits, skipped_events, fetch_failures) are left
            intact.  When False (default), all tables are dropped — this is the
            full-pipeline rebuild behaviour used by main.py.
    """
    cursor = conn.cursor()

    if rebuild:
        if cvm_only:
            logger.info("Dropping CVM tables only (prices and COTAHIST tables preserved)...")
            cursor.execute("DROP TABLE IF EXISTS fundamentals_monthly")
            cursor.execute("DROP TABLE IF EXISTS fundamentals_pit")
            cursor.execute("DROP TABLE IF EXISTS cvm_filings")
            cursor.execute("DROP TABLE IF EXISTS company_isin_map")
            cursor.execute("DROP TABLE IF EXISTS company_tickers_pit")
            cursor.execute("DROP TABLE IF EXISTS cvm_companies")
        else:
            logger.warning(
                "Full rebuild: dropping ALL tables including prices and corporate actions"
            )
            # Drop fundamentals_monthly first (depends on nothing but should be cleaned)
            cursor.execute("DROP TABLE IF EXISTS fundamentals_monthly")
            # Drop CVM fundamentals tables first (before existing tables)
            cursor.execute("DROP TABLE IF EXISTS fundamentals_pit")
            cursor.execute("DROP TABLE IF EXISTS cvm_filings")
            cursor.execute("DROP TABLE IF EXISTS company_isin_map")
            cursor.execute("DROP TABLE IF EXISTS company_tickers_pit")
            cursor.execute("DROP TABLE IF EXISTS cvm_companies")
            # Drop original tables
            cursor.execute("DROP TABLE IF EXISTS prices")
            cursor.execute("DROP TABLE IF EXISTS corporate_actions")
            cursor.execute("DROP TABLE IF EXISTS stock_actions")
            cursor.execute("DROP TABLE IF EXISTS detected_splits")
            cursor.execute("DROP TABLE IF EXISTS skipped_events")
            cursor.execute("DROP TABLE IF EXISTS fetch_failures")

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
        INDEX_PRICES_TICKER,
        INDEX_PRICES_ISIN,
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
    ]
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
    additive = [
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS quotation_factor BIGINT DEFAULT 1",
        "ALTER TABLE cvm_companies ADD COLUMN IF NOT EXISTS listing_date DATE",
        "ALTER TABLE cvm_companies ADD COLUMN IF NOT EXISTS delisting_date DATE",
        "ALTER TABLE cvm_companies ADD COLUMN IF NOT EXISTS ticker_root TEXT",
        "ALTER TABLE fundamentals_pit ADD COLUMN IF NOT EXISTS net_income_ttm DOUBLE PRECISION",
        "ALTER TABLE fundamentals_monthly ADD COLUMN IF NOT EXISTS net_income_ttm DOUBLE PRECISION",
        "ALTER TABLE fundamentals_pit ADD COLUMN IF NOT EXISTS shares_on DOUBLE PRECISION",
        "ALTER TABLE fundamentals_pit ADD COLUMN IF NOT EXISTS shares_pn DOUBLE PRECISION",
    ]
    for stmt in additive:
        cursor.execute(stmt)
    cursor.execute(INDEX_CVM_COMPANIES_TICKER_ROOT)
    # Backfill ticker_root like the SQLite path (idempotent: only fills NULLs).
    cursor.execute(
        "UPDATE cvm_companies SET ticker_root = SUBSTR(ticker, 1, 4)"
        " WHERE ticker IS NOT NULL AND ticker_root IS NULL"
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply non-destructive schema migrations for existing databases."""
    cursor = conn.cursor()

    # Add quotation_factor to prices if missing
    cursor.execute("PRAGMA table_info(prices)")
    prices_cols = {row[1] for row in cursor.fetchall()}
    if "quotation_factor" not in prices_cols:
        logger.info("Migrating: adding quotation_factor column to prices table")
        cursor.execute(
            "ALTER TABLE prices ADD COLUMN quotation_factor INTEGER DEFAULT 1"
        )

    # CVM fundamentals tables are created by CREATE TABLE IF NOT EXISTS above.
    # Add listing_date / delisting_date to cvm_companies if missing (Task 03 migration).
    cursor.execute("PRAGMA table_info(cvm_companies)")
    cvm_companies_cols = {row[1] for row in cursor.fetchall()}
    if "listing_date" not in cvm_companies_cols:
        logger.info("Migrating: adding listing_date column to cvm_companies")
        cursor.execute("ALTER TABLE cvm_companies ADD COLUMN listing_date DATE")
    if "delisting_date" not in cvm_companies_cols:
        logger.info("Migrating: adding delisting_date column to cvm_companies")
        cursor.execute("ALTER TABLE cvm_companies ADD COLUMN delisting_date DATE")
    if "ticker_root" not in cvm_companies_cols:
        logger.info("Migrating: adding ticker_root column to cvm_companies")
        cursor.execute("ALTER TABLE cvm_companies ADD COLUMN ticker_root TEXT")
        cursor.execute(INDEX_CVM_COMPANIES_TICKER_ROOT)
        cursor.execute(
            "UPDATE cvm_companies SET ticker_root = SUBSTR(ticker, 1, 4)"
            " WHERE ticker IS NOT NULL AND ticker_root IS NULL"
        )

    # Ensure ticker_root index exists for both fresh and migrated DBs.
    cursor.execute(INDEX_CVM_COMPANIES_TICKER_ROOT)

    # company_isin_map: created via SCHEMA_COMPANY_ISIN_MAP above (CREATE TABLE IF NOT EXISTS).
    # Ensure indexes exist for existing DBs that were created before this table was added.
    cursor.execute(INDEX_COMPANY_ISIN_MAP_CNPJ)
    cursor.execute(INDEX_COMPANY_ISIN_MAP_ISIN)
    cursor.execute(INDEX_COMPANY_ISIN_MAP_TICKER)
    cursor.execute(INDEX_COMPANY_ISIN_MAP_PRIMARY)

    # fundamentals_monthly: created via SCHEMA_FUNDAMENTALS_MONTHLY (CREATE TABLE IF NOT EXISTS).
    # Safe to call on existing DBs that already have the table.
    cursor.execute(SCHEMA_FUNDAMENTALS_MONTHLY)
    cursor.execute(INDEX_FUNDAMENTALS_MONTHLY_TICKER)
    cursor.execute(INDEX_FUNDAMENTALS_MONTHLY_MONTH_END)

    # company_tickers_pit: created via SCHEMA_COMPANY_TICKERS_PIT above; ensure
    # it exists for DBs initialized before the FCA ingestion was added.
    cursor.execute(SCHEMA_COMPANY_TICKERS_PIT)
    cursor.execute(INDEX_COMPANY_TICKERS_PIT_ROOT)

    # Add net_income_ttm to fundamentals_pit / fundamentals_monthly if missing
    # (DBs created before the TTM computation was added).
    for table in ("fundamentals_pit", "fundamentals_monthly"):
        cursor.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cursor.fetchall()}
        if cols and "net_income_ttm" not in cols:
            logger.info(f"Migrating: adding net_income_ttm column to {table}")
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN net_income_ttm REAL")

    # Add per-class share counts to fundamentals_pit if missing
    cursor.execute("PRAGMA table_info(fundamentals_pit)")
    pit_cols = {row[1] for row in cursor.fetchall()}
    for col in ("shares_on", "shares_pn"):
        if pit_cols and col not in pit_cols:
            logger.info(f"Migrating: adding {col} column to fundamentals_pit")
            cursor.execute(f"ALTER TABLE fundamentals_pit ADD COLUMN {col} REAL")


def _prepare_date(val) -> Optional[str]:
    """Convert date to standard string format."""
    if pd.isna(val):
        return None
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    return str(val)[:10]


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

    executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} price records")
    return len(records)


def upsert_corporate_actions(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert corporate action records into the database."""
    if df.empty:
        logger.warning("No corporate action records to upsert")
        return 0

    sql = """
        INSERT INTO corporate_actions (isin_code, event_date, event_type, value, factor, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(isin_code, event_date, event_type) DO UPDATE SET
            value = excluded.value,
            factor = excluded.factor,
            source = excluded.source
    """

    df = df.copy()
    df["event_date"] = pd.to_datetime(df["event_date"]).dt.strftime("%Y-%m-%d")
    if "value" not in df.columns:
        df["value"] = None
    if "factor" not in df.columns:
        df["factor"] = None
    if "source" not in df.columns:
        df["source"] = "B3"
    else:
        df["source"] = df["source"].fillna("B3")

    cols = ["isin_code", "event_date", "event_type", "value", "factor", "source"]
    records = df[cols].values.tolist()

    executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} corporate action records")
    return len(records)


def upsert_detected_splits(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert detected split records into the database."""
    if df.empty:
        logger.warning("No detected split records to upsert")
        return 0

    sql = """
        INSERT INTO detected_splits (isin_code, ex_date, split_factor, description)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(isin_code, ex_date) DO UPDATE SET
            split_factor = excluded.split_factor,
            description = excluded.description
    """

    df = df.copy()
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.strftime("%Y-%m-%d")
    if "description" not in df.columns:
        df["description"] = None

    cols = ["isin_code", "ex_date", "split_factor", "description"]
    records = df[cols].values.tolist()

    executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} detected split records")
    return len(records)


def upsert_stock_actions(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert stock action records (splits, reverse splits, bonuses) into the database."""
    if df.empty:
        logger.warning("No stock action records to upsert")
        return 0

    sql = """
        INSERT INTO stock_actions (isin_code, ex_date, action_type, factor, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(isin_code, ex_date, action_type) DO UPDATE SET
            factor = excluded.factor,
            source = excluded.source
    """

    df = df.copy()
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.strftime("%Y-%m-%d")
    if "source" not in df.columns:
        df["source"] = "B3"
    else:
        df["source"] = df["source"].fillna("B3")

    cols = ["isin_code", "ex_date", "action_type", "factor", "source"]
    records = df[cols].values.tolist()

    executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} stock action records")
    return len(records)


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

    sql = """
        INSERT INTO skipped_events (isin_code, event_date, label, factor, source, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(isin_code, event_date, label) DO UPDATE SET
            factor = excluded.factor,
            source = excluded.source,
            reason = excluded.reason
    """

    df = df.copy()
    df["event_date"] = pd.to_datetime(df["event_date"]).dt.strftime("%Y-%m-%d")
    if "factor" not in df.columns:
        df["factor"] = None
    if "source" not in df.columns:
        df["source"] = "B3"
    else:
        df["source"] = df["source"].fillna("B3")
    if "reason" not in df.columns:
        df["reason"] = None

    cols = ["isin_code", "event_date", "label", "factor", "source", "reason"]
    records = df[cols].values.tolist()

    executemany(conn, sql, records)
    conn.commit()

    logger.info(f"Upserted {len(records):,} skipped event records")
    return len(records)


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

    cursor.execute("SELECT COUNT(*) FROM prices")
    total_prices = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT ticker) FROM prices")
    total_tickers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT isin_code) FROM prices")
    total_isins = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(date), MAX(date) FROM prices")
    date_range = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM corporate_actions")
    total_actions = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM detected_splits")
    total_splits = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM stock_actions")
    total_stock_actions = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM skipped_events")
    total_skipped = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM fetch_failures WHERE resolved = 0")
    unresolved_failures = cursor.fetchone()[0]

    # CVM fundamentals counts (tables may not exist in very old DBs — default to 0)
    try:
        cursor.execute("SELECT COUNT(*) FROM cvm_companies")
        total_cvm_companies = cursor.fetchone()[0]
    except Exception:
        total_cvm_companies = 0

    try:
        cursor.execute("SELECT COUNT(*) FROM cvm_filings")
        total_cvm_filings = cursor.fetchone()[0]
    except Exception:
        total_cvm_filings = 0

    try:
        cursor.execute("SELECT COUNT(*) FROM fundamentals_pit")
        total_fundamentals_pit = cursor.fetchone()[0]
    except Exception:
        total_fundamentals_pit = 0

    return {
        "total_prices": total_prices,
        "total_tickers": total_tickers,
        "total_isins": total_isins,
        "date_range": (date_range[0], date_range[1]),
        "total_corporate_actions": total_actions,
        "total_detected_splits": total_splits,
        "total_stock_actions": total_stock_actions,
        "total_skipped_events": total_skipped,
        "unresolved_fetch_failures": unresolved_failures,
        "total_cvm_companies": total_cvm_companies,
        "total_cvm_filings": total_cvm_filings,
        "total_fundamentals_pit": total_fundamentals_pit,
    }
