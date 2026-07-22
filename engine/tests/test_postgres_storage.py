"""
Postgres round-trip proof for the ported storage layer.

Skipped automatically unless ENGINE_DATABASE_URL points at Postgres. When it
does, the module spins up a throwaway ``engine_test`` database (via the
superuser DSN), creates the full schema there, round-trips a realistic sample
through the storage APIs (prices insert+upsert+read, corporate actions,
fundamentals_pit write+read, and the read path backtests/core/data.py uses),
and asserts the pandas frames are identical to the SQLite equivalent.

Run it with, e.g.:
    ENGINE_DATABASE_URL=postgresql://postgres:devpassword@localhost:5432/b3 \
        pytest tests/test_postgres_storage.py -v
"""
from __future__ import annotations

import contextlib
import os
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from b3_pipeline import cvm_storage, storage
from backtests.core import data as bt_data

pytestmark = pytest.mark.skipif(
    not storage.using_postgres(),
    reason="ENGINE_DATABASE_URL is not a postgresql:// DSN",
)

_DATES = pd.date_range("2020-01-31", "2020-06-30", freq="ME")


def _swap_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + dbname, parts.query, parts.fragment))


@contextlib.contextmanager
def _env(url):
    """Temporarily point (or unset) ENGINE_DATABASE_URL."""
    old = os.environ.get("ENGINE_DATABASE_URL")
    if url is None:
        os.environ.pop("ENGINE_DATABASE_URL", None)
    else:
        os.environ["ENGINE_DATABASE_URL"] = url
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("ENGINE_DATABASE_URL", None)
        else:
            os.environ["ENGINE_DATABASE_URL"] = old


# ── Sample data (identical for both backends) ─────────────────────────────────

def _prices_df() -> pd.DataFrame:
    rows = []
    for tkr, base in (("PETR3", 30.0), ("VALE3", 60.0)):
        for i, dt in enumerate(_DATES):
            px = base + i
            rows.append({
                "ticker": tkr,
                "isin_code": f"BR{tkr}ACNOR0",
                "date": dt.strftime("%Y-%m-%d"),
                "open": px, "high": px, "low": px, "close": px,
                "volume": 1_000_000 + i,
                "quotation_factor": 1,
            })
    return pd.DataFrame(rows)


def _adj_df() -> pd.DataFrame:
    """adj_close = split_adj_* = close, so load_b3_data returns real values."""
    p = _prices_df()
    for col in ("split_adj_open", "split_adj_high", "split_adj_low",
                "split_adj_close", "adj_close"):
        p[col] = p["close"]
    return p


def _corp_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"isin_code": "BRPETR3ACNOR0", "event_date": "2020-03-15",
         "event_type": "CASH_DIVIDEND", "value": 1.25, "factor": None, "source": "B3"},
        {"isin_code": "BRVALE3ACNOR0", "event_date": "2020-05-20",
         "event_type": "JCP", "value": 0.80, "factor": None, "source": "B3"},
    ])


def _fund_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"filing_id": "PETR_DFP_2019", "cnpj": "33000167000101", "ticker": "PETR",
         "period_end": "2019-12-31", "filing_date": "2020-02-20", "filing_version": 1,
         "doc_type": "DFP", "fiscal_year": 2019, "quarter": None,
         "revenue": 302245.0, "net_income": 40137.0, "equity": 285000.0,
         "shares_outstanding": 13_044_000_000.0},
        {"filing_id": "VALE_DFP_2019", "cnpj": "33592510000154", "ticker": "VALE",
         "period_end": "2019-12-31", "filing_date": "2020-03-25", "filing_version": 1,
         "doc_type": "DFP", "fiscal_year": 2019, "quarter": None,
         "revenue": 148640.0, "net_income": 6844.0, "equity": 150000.0,
         "shares_outstanding": 5_284_000_000.0},
    ])


def _populate(conn) -> None:
    storage.init_db(conn, rebuild=True)
    storage.upsert_prices(conn, _prices_df())
    storage.update_adjusted_columns(conn, _adj_df())
    storage.upsert_corporate_actions(conn, _corp_df())
    cvm_storage.upsert_fundamentals_pit(conn, _fund_df())


def _norm_date(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.copy()
    out[col] = pd.to_datetime(out[col]).astype("datetime64[ns]")
    return out.reset_index(drop=True)


def _norm_wide(df: pd.DataFrame) -> pd.DataFrame:
    """Unify the DatetimeIndex resolution.

    Postgres DATE columns arrive as datetime64[s]; SQLite date strings parse to
    datetime64[us] under pandas 3.x. Same instants, different unit — align them.
    """
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index).as_unit("ns")
    return out


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pg_url():
    """Create a throwaway engine_test database and yield its DSN."""
    import psycopg
    base = os.environ["ENGINE_DATABASE_URL"]
    admin = _swap_db(base, "postgres")
    test_url = _swap_db(base, "engine_test")

    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS engine_test")
        c.execute("CREATE DATABASE engine_test")
    try:
        yield test_url
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'engine_test' AND pid <> pg_backend_pid()"
            )
            c.execute("DROP DATABASE IF EXISTS engine_test")


@pytest.fixture
def pg_conn(pg_url):
    """Fresh engine_test schema + ENGINE_DATABASE_URL pinned to it for the test."""
    with _env(pg_url):
        conn = storage.get_connection()
        assert storage.is_postgres(conn)
        storage.init_db(conn, rebuild=True)
        try:
            yield conn
        finally:
            conn.close()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_prices_insert_upsert_read(pg_conn):
    n = storage.upsert_prices(pg_conn, _prices_df())
    assert n == len(_DATES) * 2

    got = storage.get_all_prices(pg_conn)
    assert len(got) == len(_DATES) * 2
    assert set(got["ticker"]) == {"PETR3", "VALE3"}

    # Upsert path: re-insert PETR3's first row with a new close, confirm update.
    changed = _prices_df()
    changed.loc[changed["ticker"] == "PETR3", "close"] = 999.0
    changed.loc[changed["ticker"] == "PETR3", ["open", "high", "low"]] = 999.0
    storage.upsert_prices(pg_conn, changed)

    got2 = storage.get_all_prices(pg_conn)
    assert len(got2) == len(_DATES) * 2  # no new rows — updated in place
    petr = got2[got2["ticker"] == "PETR3"]
    assert (petr["close"] == 999.0).all()


def test_corporate_actions_roundtrip(pg_conn):
    n = storage.upsert_corporate_actions(pg_conn, _corp_df())
    assert n == 2
    got = storage.get_all_corporate_actions(pg_conn)
    assert set(got["event_type"]) == {"CASH_DIVIDEND", "JCP"}
    # idempotent upsert
    storage.upsert_corporate_actions(pg_conn, _corp_df())
    assert len(storage.get_all_corporate_actions(pg_conn)) == 2


def test_fundamentals_pit_write_and_read(pg_conn, pg_url):
    cvm_storage.upsert_fundamentals_pit(pg_conn, _fund_df())
    storage.upsert_prices(pg_conn, _prices_df())  # for the rebalance-date grid

    # data.py read path routes through the resolver (env is engine_test here).
    wide = bt_data.load_fundamentals_pit(None, "revenue", "2020-01-01", "2020-06-30", freq="ME")
    assert "PETR" in wide.columns and "VALE" in wide.columns
    assert wide["PETR"].dropna().iloc[-1] == 302245.0


def test_reads_match_sqlite(pg_conn, tmp_path):
    """The pandas read paths must return frames equal to the SQLite equivalent."""
    _populate(pg_conn)

    pg_prices = storage.get_all_prices(pg_conn)
    pg_corp = storage.get_all_corporate_actions(pg_conn)
    pg_adj, pg_close, pg_vol = bt_data.load_b3_data(None, "2020-01-01", "2020-06-30")
    pg_rev = bt_data.load_fundamentals_pit(None, "revenue", "2020-01-01", "2020-06-30")

    # SQLite reference — env unset so the resolver uses the temp file.
    with _env(None):
        sqlite_path = str(tmp_path / "ref.sqlite")
        sconn = storage.get_connection(sqlite_path)
        _populate(sconn)
        sq_prices = storage.get_all_prices(sconn)
        sq_corp = storage.get_all_corporate_actions(sconn)
        sq_adj, sq_close, sq_vol = bt_data.load_b3_data(sqlite_path, "2020-01-01", "2020-06-30")
        sq_rev = bt_data.load_fundamentals_pit(sqlite_path, "revenue", "2020-01-01", "2020-06-30")
        sconn.close()

    assert_frame_equal(
        _norm_date(pg_prices, "date"), _norm_date(sq_prices, "date"), check_dtype=False
    )
    assert_frame_equal(
        _norm_date(pg_corp, "event_date"), _norm_date(sq_corp, "event_date"), check_dtype=False
    )
    assert_frame_equal(_norm_wide(pg_close), _norm_wide(sq_close), check_dtype=False)
    assert_frame_equal(_norm_wide(pg_vol), _norm_wide(sq_vol), check_dtype=False)
    assert_frame_equal(_norm_wide(pg_adj), _norm_wide(sq_adj), check_dtype=False)
    assert_frame_equal(_norm_wide(pg_rev), _norm_wide(sq_rev), check_dtype=False)
