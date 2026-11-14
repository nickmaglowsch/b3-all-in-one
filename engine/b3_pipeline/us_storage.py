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
    " ON fundamentals_pit_us(ticker);")
INDEX_FUNDAMENTALS_PIT_US_FILING_DATE = (
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_us_filing_date"
    " ON fundamentals_pit_us(filing_date);")


# Where the per-symbol price backfill has got to.
#
# The US plane buys its bars one symbol at a time from a metered API. On the
# free tier that is 25 requests a day against ~11k symbols, so a run is always a
# slice of the job and the only thing that makes the slices add up is writing
# down where each one stopped — inside the loop, before the next request, so a
# run killed at request 12 of 25 resumes at 13 tomorrow instead of at 1.
#
# `adjust_state` is not a plan, it is a receipt. The free daily series is
# unadjusted, so `adj_close` is only a total-return number where the corporate
# actions endpoints actually answered. 'total_return' means both did,
# 'split_only' means the dividend half refused, 'unadjusted' means neither
# answered and adj_close is the raw close — a fact the product has to say out
# loud rather than let a backtest imply.
SCHEMA_US_PRICE_CURSOR = """
CREATE TABLE IF NOT EXISTS us_price_cursor (
    symbol TEXT NOT NULL PRIMARY KEY,
    first_bar DATE,
    last_bar DATE,
    prices_fetched_at TIMESTAMP,
    actions_fetched_at TIMESTAMP,
    adjust_state TEXT,
    status TEXT,
    error TEXT,
    attempts INTEGER DEFAULT 0
);
"""

INDEX_US_PRICE_CURSOR_STATUS = (
    "CREATE INDEX IF NOT EXISTS idx_us_price_cursor_status"
    " ON us_price_cursor(status);"
)


# Requests spent against a metered API, per day.
#
# Kept in the database rather than in the client because an in-memory counter
# double-spends: the nightly job and someone running the ingest by hand on the
# same day would each believe they had the whole budget. At 25 requests a day
# that is the difference between progress and none.
SCHEMA_API_CALL_BUDGET = """
CREATE TABLE IF NOT EXISTS api_call_budget (
    source TEXT NOT NULL,
    day DATE NOT NULL,
    spent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, day)
);
"""


# Which ticker a CIK traded under, and when.
#
# `company_tickers.json` is a snapshot of who trades today, so a company that
# stopped filing in 2009 has no ticker in it at all and its facts land under
# `CIK0000806085` — reachable by nobody, joinable to no price series. This table
# is the CVM side's `company_tickers_pit` for the US: an interval per (cik,
# ticker), filled backward from the bulk submissions archive and forward from
# the daily snapshot.
#
# NULL `start_date` means "as far back as the source reaches", NULL `end_date`
# means "still current". `source` records which feed made the claim, because the
# backward one is coarse (bounded by filing history, not by trading) and a
# reader deserves to know which kind it is holding.
SCHEMA_US_TICKER_PIT = """
CREATE TABLE IF NOT EXISTS us_ticker_pit (
    cik TEXT NOT NULL,
    ticker TEXT NOT NULL,
    start_date DATE,
    end_date DATE,
    exchange TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (cik, ticker)
);
"""

INDEX_US_TICKER_PIT_CIK = (
    "CREATE INDEX IF NOT EXISTS idx_us_ticker_pit_cik ON us_ticker_pit(cik);")
INDEX_US_TICKER_PIT_TICKER = (
    "CREATE INDEX IF NOT EXISTS idx_us_ticker_pit_ticker ON us_ticker_pit(ticker);")


# Splits and dividends, kept rather than re-fetched.
#
# The daily series is refreshed far more often than corporate actions change,
# and re-asking for both every time would spend two thirds of a free tier's
# budget on answers that did not move. But the adjustment has to be recomputed
# whenever the bars are rewritten, so the events have to be *somewhere* — and
# storing them is also what #40 asks for in its own right.
#
# `value` is a ratio for a split (old-over-new, so a 4:1 is 0.25) and a
# per-share amount for a dividend. One column because they are never mixed in a
# calculation; `kind` says which reading applies.
SCHEMA_US_CORPORATE_ACTIONS = """
CREATE TABLE IF NOT EXISTS us_corporate_actions (
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    ex_date DATE NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, kind, ex_date)
);
"""

INDEX_US_CORPORATE_ACTIONS_SYMBOL = (
    "CREATE INDEX IF NOT EXISTS idx_us_corporate_actions_symbol"
    " ON us_corporate_actions(symbol);"
)


# Bulk passes that ran to their end, by name — read by `us_main` instead of
# inferring "bootstrapped" from its own output. See `us_main._bulk_done`.
SCHEMA_US_INGEST_MILESTONES = """
CREATE TABLE IF NOT EXISTS us_ingest_milestones (
    name TEXT NOT NULL PRIMARY KEY, reached_at TIMESTAMP NOT NULL, detail TEXT
);
"""


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
    SCHEMA_US_PRICE_CURSOR,
    INDEX_US_PRICE_CURSOR_STATUS,
    SCHEMA_API_CALL_BUDGET,
    SCHEMA_US_TICKER_PIT,
    INDEX_US_TICKER_PIT_CIK,
    INDEX_US_TICKER_PIT_TICKER,
    SCHEMA_US_CORPORATE_ACTIONS,
    INDEX_US_CORPORATE_ACTIONS_SYMBOL,
    SCHEMA_US_INGEST_MILESTONES,
]


# ── The metered-API budget ────────────────────────────────────────────────────

def calls_spent(conn, source: str, day) -> int:
    """Requests already charged to `source` today."""
    from . import storage

    df = storage.read_sql(
        "SELECT spent FROM api_call_budget WHERE source = ? AND day = ?",
        conn, params=(source, str(day)))
    return int(df.iloc[0]["spent"]) if not df.empty else 0


def charge_call(conn, source: str, day) -> int:
    """Charge one request and return the new total.

    Charged before the request goes out, not after. An attempt that times out
    still consumed the vendor's allowance as far as anyone here can tell, and a
    budget that undercounts is one that gets the key throttled — the failure
    mode a counter exists to prevent.
    """
    from . import storage

    storage.execute(
        conn,
        "INSERT INTO api_call_budget (source, day, spent) VALUES (?, ?, 1)"
        " ON CONFLICT(source, day) DO UPDATE SET spent = api_call_budget.spent + 1",
        (source, str(day)))
    conn.commit()
    return calls_spent(conn, source, day)


# ── The per-symbol price cursor ───────────────────────────────────────────────

CURSOR_COLUMNS = [
    "symbol", "first_bar", "last_bar", "prices_fetched_at", "actions_fetched_at",
    "adjust_state", "status", "error", "attempts",
]

_CURSOR_STAGE_TYPES = (
    "symbol TEXT, first_bar DATE, last_bar DATE, prices_fetched_at TIMESTAMP,"
    " actions_fetched_at TIMESTAMP, adjust_state TEXT, status TEXT, error TEXT,"
    " attempts BIGINT"
)


def upsert_price_cursor(conn, df: pd.DataFrame) -> int:
    """Record where the backfill got to, one symbol at a time.

    Narrow like `upsert_instruments`, and for the same reason: a run that only
    refreshed corporate actions must not blank the bar range a previous run
    established.
    """
    if df.empty:
        return 0
    supplied = [c for c in CURSOR_COLUMNS if c in df.columns]
    df = df.copy()
    for col in CURSOR_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in ("first_bar", "last_bar"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    return _upsert_frame(
        conn, "us_price_cursor", df, ["symbol"], CURSOR_COLUMNS,
        _CURSOR_STAGE_TYPES, "_uspc_stage", update_cols=supplied)


def load_price_cursor(conn) -> pd.DataFrame:
    """The whole cursor table. Small — one row per US symbol."""
    from . import storage

    return storage.read_sql("SELECT * FROM us_price_cursor", conn)


def adjust_state_counts(conn) -> dict:
    """How many symbols carry each adjustment state.

    This is what `GET /market/status` reports and what the backtest warns on: a
    plane where most symbols are 'unadjusted' cannot support a total-return
    comparison, and saying so is cheaper than being wrong quietly.
    """
    from . import storage

    df = storage.read_sql(
        "SELECT adjust_state, COUNT(*) AS n FROM us_price_cursor"
        " WHERE adjust_state IS NOT NULL GROUP BY adjust_state", conn)
    return {r["adjust_state"]: int(r["n"]) for _, r in df.iterrows()}


# ── CIK -> ticker, with validity ──────────────────────────────────────────────

TICKER_PIT_COLUMNS = ["cik", "ticker", "start_date", "end_date", "exchange", "source"]

_TICKER_PIT_STAGE_TYPES = (
    "cik TEXT, ticker TEXT, start_date DATE, end_date DATE, exchange TEXT,"
    " source TEXT"
)


def upsert_ticker_pit(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    supplied = [c for c in TICKER_PIT_COLUMNS if c in df.columns]
    df = df.copy()
    for col in TICKER_PIT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in ("start_date", "end_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["cik", "ticker", "source"])
    n = _upsert_frame(
        conn, "us_ticker_pit", df, ["cik", "ticker"], TICKER_PIT_COLUMNS,
        _TICKER_PIT_STAGE_TYPES, "_utp_stage", update_cols=supplied)
    logger.info(f"Upserted {n:,} CIK->ticker intervals")
    return n


def load_ticker_pit(conn, cik: str | None = None) -> pd.DataFrame:
    from . import storage

    where, params = ("", ()) if cik is None else (" WHERE cik = ?", (cik,))
    return storage.read_sql(
        f"SELECT * FROM us_ticker_pit{where} ORDER BY cik, start_date",
        conn, params=params)


# ── Corporate actions ─────────────────────────────────────────────────────────

_ACTIONS_STAGE_TYPES = (
    "symbol TEXT, kind TEXT, ex_date DATE, value DOUBLE PRECISION, source TEXT")


def upsert_corporate_actions(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df = df.copy()
    df["ex_date"] = pd.to_datetime(df["ex_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["symbol", "kind", "ex_date", "value"])
    return _upsert_frame(
        conn, "us_corporate_actions", df, ["symbol", "kind", "ex_date"],
        ["symbol", "kind", "ex_date", "value", "source"],
        _ACTIONS_STAGE_TYPES, "_usca_stage")


def load_corporate_actions(conn, symbol: str, kind: str) -> pd.DataFrame:
    """One symbol's stored events of one kind, oldest first."""
    from . import storage

    return storage.read_sql(
        "SELECT ex_date, value FROM us_corporate_actions"
        " WHERE symbol = ? AND kind = ? ORDER BY ex_date",
        conn, params=(symbol, kind))
