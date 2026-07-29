"""Shared fixtures for the engine tests.

The important one is DSN isolation. `storage.get_connection` resolves the backend
from ENGINE_DATABASE_URL *before* it looks at the db_path it was handed — that
precedence is deliberate and production depends on it, because a strategy carries
a default SQLite db_path in its parameters and still has to reach the Postgres
plane.

The consequence for tests is that any module which builds a temporary SQLite
database and passes its path is silently redirected to Postgres whenever a
developer has that variable exported. 22 tests across 7 modules failed that way —
they were asserting against whatever happened to be in the real market plane
instead of the three rows they had just inserted. CI never sets the variable, so
the whole class of failure was invisible there and only appeared on a dev box
that had it in the shell.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pytest

from b3_pipeline import storage


def seed_cvm_company(
    conn: sqlite3.Connection,
    cnpj: str,
    ticker: Optional[str],
    company_name: Optional[str],
    cvm_code: Optional[str],
    b3_trading_name: Optional[str],
) -> None:
    """Put one row in cvm_companies. Test-only: the pipeline writes in bulk.

    Idempotent on cnpj, so a test can seed the same company twice to check that
    the second write updates rather than raising.
    """
    sql = """
        INSERT INTO cvm_companies (cnpj, ticker, ticker_root, company_name, cvm_code, b3_trading_name, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cnpj) DO UPDATE SET
            ticker = excluded.ticker,
            ticker_root = excluded.ticker_root,
            company_name = excluded.company_name,
            cvm_code = excluded.cvm_code,
            b3_trading_name = excluded.b3_trading_name,
            updated_at = CURRENT_TIMESTAMP
    """
    storage.execute(
        conn, sql,
        (cnpj, ticker, ticker[:4] if ticker else None, company_name, cvm_code, b3_trading_name),
    )
    conn.commit()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "postgres: test genuinely targets the Postgres plane and needs "
        "ENGINE_DATABASE_URL left intact",
    )


@pytest.fixture(autouse=True)
def isolate_engine_dsn(request, monkeypatch):
    """Hide ENGINE_DATABASE_URL from tests that build their own SQLite fixture.

    Opt back in with @pytest.mark.postgres (module- or test-level) when the point
    of the test *is* the Postgres backend. Those modules also carry a skipif on
    storage.using_postgres(), which is evaluated at collection time — before this
    fixture runs — so the marker is what keeps the variable available to the test
    body itself.
    """
    if request.node.get_closest_marker("postgres"):
        return
    monkeypatch.delenv("ENGINE_DATABASE_URL", raising=False)


