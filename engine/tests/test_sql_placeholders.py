"""Literal `%` in SQL, on the Postgres path.

The CRYPTO market spec filters the universe with `symbol LIKE '%USDT'` (Binance
lists the same coin against a dozen quote assets). psycopg scans a query for
pyformat placeholders whenever it is given parameters, so that literal percent
read as the placeholder `%U` and every crypto backtest died in
load_market_hlc_data with:

    ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%U'

psycopg is not installed on the SQLite-only test path, so these exercise
storage._xlate directly with a stand-in connection — `is_postgres` is just
"not a sqlite3.Connection".
"""
from __future__ import annotations

import re
import sqlite3

from b3_pipeline import storage
from backtests.core import markets


class _FakePgConn:
    """Anything that is not a sqlite3.Connection is Postgres to storage."""


_PG = _FakePgConn()


def test_sqlite_is_untouched_percent_included():
    sql = "SELECT * FROM t WHERE symbol LIKE '%USDT' AND date >= :start"
    conn = sqlite3.connect(":memory:")
    try:
        assert storage._xlate(conn, sql, {"start": "2024-01-01"}) == sql
    finally:
        conn.close()


def test_literal_percent_is_escaped_when_params_are_passed():
    out = storage._xlate(_PG, "SELECT 1 WHERE symbol LIKE '%USDT' AND d >= :start",
                         {"start": "2024-01-01"})
    assert out == "SELECT 1 WHERE symbol LIKE '%%USDT' AND d >= %(start)s"


def test_placeholder_percent_is_not_double_escaped():
    """Escaping runs before the rewrites, so the `%` they introduce survives."""
    assert storage._xlate(_PG, "SELECT :a, ?", {"a": 1}) == "SELECT %(a)s, %s"


def test_literal_percent_is_left_alone_without_params():
    """psycopg only parses placeholders when given params — `%%` would reach the
    server verbatim on an unparameterised query."""
    sql = "SELECT COUNT(*) FROM instruments WHERE symbol LIKE 'CIK%'"
    assert storage._xlate(_PG, sql) == sql


def test_the_crypto_hlc_query_translates():
    """The exact shape backtests/core/data.py builds for CRYPTO."""
    spec = markets.get("CRYPTO")
    assert "%" in spec.ticker_sql, "spec no longer carries the literal this guards"
    sql = (f"SELECT date FROM {spec.table} WHERE date >= :start AND date <= :end"
           f" AND market = :market AND {spec.ticker_sql}")
    out = storage._xlate(_PG, sql, {"start": "a", "end": "b", "market": "CRYPTO"})
    assert "%%USDT" in out
    assert "%(start)s" in out and "%(end)s" in out and "%(market)s" in out
    assert _bad_placeholders(out) == []


def _bad_placeholders(sql: str) -> list:
    """Every `%` psycopg would reject — its own rule, without needing psycopg.

    psycopg accepts exactly `%s`, `%b`, `%t`, `%(name)s|b|t` and the escape `%%`;
    anything else raises ProgrammingError. Scanning left to right is what makes
    `%%USDT` read as one escape plus a `U`, not as `%U`.
    """
    bad, i = [], 0
    while i < len(sql):
        if sql[i] != "%":
            i += 1
            continue
        m = _PLACEHOLDER.match(sql, i)
        if m is None:
            bad.append(sql[i:i + 2])
            i += 1
        else:
            i = m.end()
    return bad


_PLACEHOLDER = re.compile(r"%(?:%|(?:\([^)]*\))?[sbt])")


def test_the_placeholder_scanner_agrees_with_psycopg():
    """Guards the guard: the raw crypto SQL is what psycopg choked on."""
    assert _bad_placeholders("symbol LIKE '%USDT'") == ["%U"]
    assert _bad_placeholders("symbol LIKE '%%USDT'") == []
    assert _bad_placeholders("SELECT %s, %(a)s, %b") == []
