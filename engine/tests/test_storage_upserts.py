"""The B3 corporate-action upserts in storage, on SQLite.

test_postgres_storage covers the same writers against a live Postgres and is
skipped without one, so the defaults, the date normalisation and the conflict
behaviour are pinned here as well.
"""
from __future__ import annotations

import logging
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import storage


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    storage.init_db(c)
    yield c
    c.close()


def _rows(conn, sql):
    return conn.execute(sql).fetchall()


def test_corporate_actions_defaults_dates_and_conflict_update(conn):
    df = pd.DataFrame([
        {"isin_code": "BRA", "event_date": pd.Timestamp("2024-01-02"), "event_type": "DIV",
         "source": None},
        {"isin_code": "BRB", "event_date": "2024-01-03", "event_type": "JCP",
         "source": "CVM"},
    ])
    assert storage.upsert_corporate_actions(conn, df) == 2
    assert _rows(conn, "SELECT isin_code, event_date, event_type, value, factor, source"
                       " FROM corporate_actions ORDER BY isin_code") == [
        ("BRA", "2024-01-02", "DIV", None, None, "B3"),
        ("BRB", "2024-01-03", "JCP", None, None, "CVM"),
    ]
    storage.upsert_corporate_actions(conn, pd.DataFrame([
        {"isin_code": "BRA", "event_date": "2024-01-02", "event_type": "DIV",
         "value": 1.5, "factor": 0.9}]))
    assert _rows(conn, "SELECT value, factor, source FROM corporate_actions"
                       " WHERE isin_code='BRA'") == [(1.5, 0.9, "B3")]


def test_detected_splits_default_description_and_conflict_update(conn):
    df = pd.DataFrame([{"isin_code": "BRA", "ex_date": "2024-01-02", "split_factor": 2.0}])
    assert storage.upsert_detected_splits(conn, df) == 1
    storage.upsert_detected_splits(conn, df.assign(split_factor=4.0, description="x"))
    assert _rows(conn, "SELECT isin_code, ex_date, split_factor, description"
                       " FROM detected_splits") == [("BRA", "2024-01-02", 4.0, "x")]


def test_stock_actions_default_source(conn):
    df = pd.DataFrame([{"isin_code": "BRA", "ex_date": "2024-01-02",
                        "action_type": "SPLIT", "factor": 2.0}])
    assert storage.upsert_stock_actions(conn, df) == 1
    storage.upsert_stock_actions(conn, df.assign(factor=3.0, source=[None]))
    assert _rows(conn, "SELECT isin_code, ex_date, action_type, factor, source"
                       " FROM stock_actions") == [("BRA", "2024-01-02", "SPLIT", 3.0, "B3")]


def test_skipped_events_defaults_and_conflict_update(conn):
    df = pd.DataFrame([{"isin_code": "BRA", "event_date": "2024-01-02", "label": "ODD"}])
    assert storage.upsert_skipped_events(conn, df) == 1
    assert _rows(conn, "SELECT isin_code, event_date, label, factor, source, reason"
                       " FROM skipped_events") == [("BRA", "2024-01-02", "ODD", None, "B3", None)]
    storage.upsert_skipped_events(conn, df.assign(factor=2.0, source="X", reason="why"))
    assert _rows(conn, "SELECT factor, source, reason FROM skipped_events") == [(2.0, "X", "why")]


def test_empty_frames_write_nothing_and_only_skipped_events_stay_quiet(conn, caplog):
    with caplog.at_level(logging.WARNING, logger=storage.logger.name):
        assert storage.upsert_corporate_actions(conn, pd.DataFrame()) == 0
        assert storage.upsert_detected_splits(conn, pd.DataFrame()) == 0
        assert storage.upsert_stock_actions(conn, pd.DataFrame()) == 0
        assert storage.upsert_skipped_events(conn, pd.DataFrame()) == 0
    assert [r.getMessage() for r in caplog.records] == [
        "No corporate action records to upsert",
        "No detected split records to upsert",
        "No stock action records to upsert",
    ]


def test_success_is_logged_with_the_row_count(conn, caplog):
    df = pd.DataFrame([{"isin_code": "BRA", "ex_date": "2024-01-02", "split_factor": 2.0}])
    with caplog.at_level(logging.INFO, logger=storage.logger.name):
        storage.upsert_detected_splits(conn, df)
    assert "Upserted 1 detected split records" in [r.getMessage() for r in caplog.records]


def test_upsert_sql_updates_every_non_key_column():
    sql = storage._upsert_sql("t", ["a", "b", "c", "d"], ["a", "b"])
    assert " ".join(sql.split()) == (
        "INSERT INTO t (a, b, c, d) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(a, b) DO UPDATE SET c = excluded.c, d = excluded.d")
