"""Schema and writers for the tables only the US plane has.

Split out of `market_storage` rather than piled into it, and the seam is which
markets a table serves. `instruments` and `market_prices` are shared by every
non-B3 market and stay there; everything here — SEC fundamentals, the CIK-to-
ticker history, the price cursor, the API budget — exists because the US plane
buys its data one symbol at a time from a metered source and reads its
fundamentals out of EDGAR. Crypto needs none of it.

Same rules as the sibling module: DDL is additive, nothing here is in
`init_db(rebuild=True)`'s drop list (this archive is built forward and cannot be
re-fetched), and `storage` helpers are imported inside the functions because
`storage` imports this module to build its DDL list.
"""

import logging

import pandas as pd

from .market_storage import _upsert_frame

logger = logging.getLogger(__name__)


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


# Appended to storage.init_db's ddl_statements alongside market_storage.DDL, so
# these run in the same pass and through the same _pg_ddl translation.
DDL = [
    SCHEMA_FUNDAMENTALS_PIT_US,
    INDEX_FUNDAMENTALS_PIT_US_CIK,
    INDEX_FUNDAMENTALS_PIT_US_TICKER,
    INDEX_FUNDAMENTALS_PIT_US_FILING_DATE,
]
