"""idx_prices_ticker duplicated the primary key's leading column.

`prices` is PRIMARY KEY (ticker, date), which already serves every
ticker-leading lookup — an equality probe on ticker, a range scan within one
ticker, an ORDER BY ticker. A second index on (ticker) alone answers no query
the PK cannot, and every bulk write into the largest table in the database paid
to maintain it.

These pin that it is neither created on a fresh database nor left behind on an
existing one, and that ticker lookups still reach an index.
"""
from __future__ import annotations

import sqlite3

import pytest

from b3_pipeline import storage

# Targets the Postgres plane: conftest's isolate_engine_dsn keeps
# ENGINE_DATABASE_URL in place for anything marked `postgres`.
pytestmark = pytest.mark.postgres


def _indexes(conn) -> set[str]:
    cur = conn.cursor()
    if storage.is_postgres(conn):
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'prices'")
    else:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'prices'")
    return {r[0] for r in cur.fetchall() if r[0]}


def test_fresh_sqlite_db_has_no_redundant_ticker_index(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "fresh.sqlite"))
    storage.init_db(conn)
    conn.commit()
    names = _indexes(conn)
    conn.close()
    assert "idx_prices_ticker" not in names
    # The ones that do earn their keep are still there.
    assert "idx_prices_date" in names
    assert "idx_prices_isin" in names


def test_existing_db_drops_the_redundant_index_on_init(tmp_path):
    """An already-deployed database carries the index; init_db has to remove it,
    or the cost stays for every database that ever ran the old schema."""
    db = str(tmp_path / "legacy.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    # Recreate it the way the old schema did.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker)")
    conn.commit()
    assert "idx_prices_ticker" in _indexes(conn)
    conn.close()

    conn = sqlite3.connect(db)
    storage.init_db(conn)
    conn.commit()
    names = _indexes(conn)
    conn.close()
    assert "idx_prices_ticker" not in names


def test_ticker_lookup_still_uses_an_index(tmp_path):
    """The point is that the primary key already covers this. If it did not,
    dropping the index would turn every per-ticker read into a table scan."""
    conn = sqlite3.connect(str(tmp_path / "plan.sqlite"))
    storage.init_db(conn)
    for i in range(200):
        conn.execute(
            "INSERT OR REPLACE INTO prices (ticker, isin_code, date, open, high,"
            " low, close, volume) VALUES (?, 'UNKNOWN', ?, 1, 1, 1, 1, 1)",
            (f"TICK{i % 20}", f"2024-01-{(i % 28) + 1:02d}"),
        )
    conn.commit()
    plan = " ".join(
        str(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT close FROM prices WHERE ticker = 'TICK1'"
        ).fetchall()
    )
    conn.close()
    assert "SCAN" not in plan.upper() or "INDEX" in plan.upper(), plan


@pytest.mark.skipif(not storage.using_postgres(),
                    reason="ENGINE_DATABASE_URL is not a postgresql:// DSN")
def test_postgres_plane_has_no_redundant_ticker_index():
    with storage.get_connection(None) as conn:
        storage.init_db(conn)
        conn.commit()
        assert "idx_prices_ticker" not in _indexes(conn)
