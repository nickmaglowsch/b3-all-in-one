"""`prices.volume` must hold the fractional value the parser produces.

COTAHIST's VOLTOT carries two implied decimals and parser._parse_price divides by
100, so a volume is reais-and-centavos — a genuinely fractional number.

`prices.volume` was declared INTEGER, which _pg_ddl widens to BIGINT, so Postgres
rounded every volume while SQLite's dynamic typing kept it. Two consequences:
the same pipeline stored different numbers on the two backends, and the sibling
table holding the same quantity (external_prices.volume) was already REAL — so
the schema disagreed with itself.

Volume feeds fin_vol -> adtv, which is the liquidity filter every strategy
universe is cut on, so the two backends were screening slightly different
universes.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from b3_pipeline import storage

PRICE_COLS = ["ticker", "isin_code", "date", "open", "high", "low", "close",
              "volume", "quotation_factor"]

# A volume with centavos, the shape _parse_price actually emits (VOLTOT / 100).
# Compared exactly, not with approx: at this magnitude a rounded value sits
# well inside approx's default 1e-6 relative tolerance, so the first version
# of these tests passed against the BIGINT column they were written to catch.
FRACTIONAL = 1234567.89


def _one_row(volume=FRACTIONAL) -> pd.DataFrame:
    return pd.DataFrame([dict(zip(
        PRICE_COLS,
        ("PETR4", "BRPETRACNPR6", "2024-01-02", 30.0, 30.0, 30.0, 30.0, volume, 1),
    ))])


def _volume(conn) -> float:
    cur = conn.cursor()
    cur.execute("SELECT volume FROM prices WHERE ticker = 'PETR4'")
    return cur.fetchone()[0]


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "vol.sqlite"))
    storage.init_db(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def pg_conn():
    if not storage.using_postgres():
        pytest.skip("ENGINE_DATABASE_URL is not a postgresql:// DSN")
    conn = storage.get_connection(None)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS volume_precision_test CASCADE")
    cur.execute("CREATE SCHEMA volume_precision_test")
    cur.execute("SET search_path TO volume_precision_test")
    storage.init_db(conn)
    conn.commit()
    yield conn
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS volume_precision_test CASCADE")
    conn.commit()
    conn.close()


def test_sqlite_keeps_the_centavos(sqlite_conn):
    storage.upsert_prices(sqlite_conn, _one_row())
    assert _volume(sqlite_conn) == FRACTIONAL


@pytest.mark.postgres
def test_postgres_keeps_the_centavos(pg_conn):
    storage.upsert_prices(pg_conn, _one_row())
    assert _volume(pg_conn) == FRACTIONAL


@pytest.mark.postgres
def test_both_backends_store_the_same_volume(sqlite_conn, pg_conn):
    """The divergence this is really about: one pipeline, two answers."""
    for conn in (sqlite_conn, pg_conn):
        storage.upsert_prices(conn, _one_row())
    assert _volume(sqlite_conn) == _volume(pg_conn) == FRACTIONAL


@pytest.mark.postgres
def test_fresh_postgres_schema_declares_volume_as_floating_point(pg_conn):
    cur = pg_conn.cursor()
    cur.execute(
        "SELECT data_type FROM information_schema.columns"
        " WHERE table_name = 'prices' AND column_name = 'volume'"
        "   AND table_schema = 'volume_precision_test'")
    assert cur.fetchone()[0] == "double precision"


@pytest.mark.postgres
def test_an_existing_bigint_column_is_migrated():
    """Databases already deployed carry the BIGINT column; init_db has to widen
    it, or every existing plane keeps rounding."""
    if not storage.using_postgres():
        pytest.skip("ENGINE_DATABASE_URL is not a postgresql:// DSN")
    conn = storage.get_connection(None)
    cur = conn.cursor()
    try:
        cur.execute("DROP SCHEMA IF EXISTS volume_migration_test CASCADE")
        cur.execute("CREATE SCHEMA volume_migration_test")
        cur.execute("SET search_path TO volume_migration_test")
        storage.init_db(conn)
        # Put the old type back, as an already-deployed database has it.
        cur.execute("ALTER TABLE prices ALTER COLUMN volume TYPE BIGINT")
        conn.commit()

        storage.init_db(conn)
        conn.commit()
        cur.execute(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name = 'prices' AND column_name = 'volume'"
            "   AND table_schema = 'volume_migration_test'")
        assert cur.fetchone()[0] == "double precision"

        storage.upsert_prices(conn, _one_row())
        assert _volume(conn) == FRACTIONAL
    finally:
        cur.execute("DROP SCHEMA IF EXISTS volume_migration_test CASCADE")
        conn.commit()
        conn.close()


@pytest.mark.postgres
def test_migration_does_not_rewrite_an_already_correct_column(monkeypatch):
    """ALTER TABLE ... TYPE rewrites the whole table and takes an exclusive lock.
    prices is the biggest table here and init_db runs at the start of every
    pipeline run, so the migration must fire once, not on every run."""
    if not storage.using_postgres():
        pytest.skip("ENGINE_DATABASE_URL is not a postgresql:// DSN")
    conn = storage.get_connection(None)
    cur = conn.cursor()
    try:
        cur.execute("DROP SCHEMA IF EXISTS volume_noop_test CASCADE")
        cur.execute("CREATE SCHEMA volume_noop_test")
        cur.execute("SET search_path TO volume_noop_test")
        storage.init_db(conn)
        conn.commit()

        seen: list[str] = []
        real_execute = storage.execute

        def spy(c, sql, params=None):
            if "ALTER COLUMN volume" in sql:
                seen.append(sql)
            return real_execute(c, sql, params)

        monkeypatch.setattr(storage, "execute", spy)
        storage.init_db(conn)
        conn.commit()
        assert seen == [], f"migration re-ran on an already-correct column: {seen}"
    finally:
        cur.execute("DROP SCHEMA IF EXISTS volume_noop_test CASCADE")
        conn.commit()
        conn.close()
