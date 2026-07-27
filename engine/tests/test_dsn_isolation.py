"""The DSN-isolation fixture itself.

storage.get_connection resolves ENGINE_DATABASE_URL *before* the db_path it was
handed. That precedence is deliberate — a strategy carries a default SQLite path
in its parameters and still has to reach the Postgres plane — but it means any
test that builds a temporary SQLite database and passes its path is silently
redirected whenever a developer has the variable exported.

22 tests across 7 modules failed exactly that way: they asserted against whatever
was in the real market plane instead of the rows they had just inserted. CI never
sets the variable, so none of it was visible there.

A fixture that quietly stops working would put all of that back, so it gets its
own tests.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from b3_pipeline import storage


def test_dsn_is_hidden_from_an_unmarked_test():
    assert "ENGINE_DATABASE_URL" not in os.environ
    assert not storage.using_postgres()


@pytest.mark.postgres
def test_dsn_survives_for_a_marked_test():
    """Marked tests target the plane and must still see the variable — otherwise
    the fixture would break the very tests it is meant to leave alone."""
    if not os.environ.get("ENGINE_DATABASE_URL"):
        pytest.skip("ENGINE_DATABASE_URL is not set in this environment")
    assert storage.using_postgres()


def test_an_explicit_sqlite_path_is_honoured(tmp_path):
    """The behaviour the 22 failures were really about: a test hands
    get_connection a path it just created, and gets that database back."""
    db = str(tmp_path / "isolated.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    conn.execute(
        "INSERT INTO prices (ticker, isin_code, date, open, high, low, close,"
        " volume) VALUES ('ONLYHERE3', 'X', '2024-01-02', 1, 1, 1, 1, 1)")
    conn.commit()
    conn.close()

    with storage.get_connection(db) as c:
        tickers = storage.read_sql("SELECT DISTINCT ticker FROM prices", c)
    assert list(tickers["ticker"]) == ["ONLYHERE3"], (
        "get_connection did not return the SQLite fixture it was given")
