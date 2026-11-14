"""
Tests for Phase 4: API failure tracking.

These tests verify:
1. A fetch_failures table is created by init_db
2. record_fetch_failure inserts a row into fetch_failures
3. get_unresolved_failures returns only unresolved entries
4. resolve_fetch_failure marks an entry as resolved
"""
import logging
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import main as pipeline
from b3_pipeline import storage


def _in_memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class TestFetchFailureTracking:
    """Test the fetch_failures table operations."""

    def test_init_db_creates_fetch_failures_table(self):
        """
        GIVEN a fresh database
        WHEN init_db is called
        THEN a fetch_failures table should exist
        """
        conn = _in_memory_conn()
        storage.init_db(conn, rebuild=False)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fetch_failures'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None, "fetch_failures table should be created by init_db"

    def test_record_fetch_failure_inserts_row(self):
        """
        GIVEN a database with fetch_failures table
        WHEN record_fetch_failure is called
        THEN a row should be inserted with resolved=0
        """
        conn = _in_memory_conn()
        storage.init_db(conn, rebuild=False)

        storage.record_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany", "520 Server Error")

        cursor = conn.cursor()
        cursor.execute("SELECT company_code, endpoint, resolved FROM fetch_failures")
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) == 1, "Should have one failure record"
        assert rows[0][0] == "ARZZ"
        assert rows[0][1] == "GetListedSupplementCompany"
        assert rows[0][2] == 0, "resolved should be 0 (False)"

    def test_get_unresolved_failures(self):
        """
        GIVEN two failures, one resolved and one not
        WHEN get_unresolved_failures is called
        THEN only the unresolved one should be returned
        """
        conn = _in_memory_conn()
        storage.init_db(conn, rebuild=False)

        storage.record_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany", "520 error")
        storage.record_fetch_failure(conn, "BMGB", "GetListedSupplementCompany", "timeout")

        storage.resolve_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany")

        unresolved = storage.get_unresolved_failures(conn)
        conn.close()

        assert len(unresolved) == 1, f"Expected 1 unresolved, got {len(unresolved)}"
        assert unresolved[0]["company_code"] == "BMGB"

    def test_resolve_fetch_failure(self):
        """
        GIVEN a failure record
        WHEN resolve_fetch_failure is called
        THEN resolved should be set to 1
        """
        conn = _in_memory_conn()
        storage.init_db(conn, rebuild=False)

        storage.record_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany", "error")
        storage.resolve_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany")

        cursor = conn.cursor()
        cursor.execute("SELECT resolved FROM fetch_failures WHERE company_code='ARZZ'")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 1, "resolved should be 1 after resolve_fetch_failure"

    def test_upsert_on_duplicate_failure(self):
        """
        GIVEN an existing failure record
        WHEN record_fetch_failure is called again for the same company+endpoint
        THEN the retry_count should be incremented and resolved stays 0
        """
        conn = _in_memory_conn()
        storage.init_db(conn, rebuild=False)

        storage.record_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany", "first error")
        storage.record_fetch_failure(conn, "ARZZ", "GetListedSupplementCompany", "second error")

        cursor = conn.cursor()
        cursor.execute("SELECT retry_count FROM fetch_failures WHERE company_code='ARZZ'")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] >= 1, "retry_count should be incremented on duplicate failure"


# ── Retrying the recorded failures (main.retry_failed_companies / step 9b) ───

@pytest.fixture
def retry_world(monkeypatch, tmp_path):
    """Two recorded failures: OKAY now answers, DEAD still does not."""
    db = tmp_path / "retry.sqlite"
    monkeypatch.setattr(pipeline.storage, "get_connection", lambda: sqlite3.connect(db))
    conn = sqlite3.connect(db)
    storage.init_db(conn, rebuild=False)
    storage.record_fetch_failure(conn, "DEAD", "GetListedSupplementCompany", "x")
    storage.record_fetch_failure(conn, "OKAY", "GetListedSupplementCompany", "x")
    writes = []
    one = pd.DataFrame({"a": [1]})
    b3 = pipeline.b3_corporate_actions
    monkeypatch.setattr(b3, "fetch_company_data", lambda code, conn=None: None if code == "DEAD"
                        else {"stockDividends": [{"s": 1}], "tradingName": " OKAY SA "})
    monkeypatch.setattr(b3, "parse_stock_dividends",
                        lambda divs: (one, one, pd.DataFrame()))
    monkeypatch.setattr(b3, "fetch_cash_dividends_paginated",
                        lambda name, **kw: writes.append(("cash_fetch", name))
                        or ([{"c": 1}], True))
    monkeypatch.setattr(b3, "parse_cash_dividends",
                        lambda divs, code, isin_map: writes.append(("cash_parse", code)) or one)
    for fn in ("upsert_corporate_actions", "upsert_stock_actions", "upsert_skipped_events"):
        monkeypatch.setattr(pipeline.storage, fn,
                            lambda c, df, _fn=fn: writes.append((_fn, len(df))))
    return conn, writes


EXPECTED_WRITES = [
    ("upsert_corporate_actions", 1), ("upsert_stock_actions", 1),
    ("cash_fetch", "OKAY SA"), ("cash_parse", "OKAY"), ("upsert_corporate_actions", 1),
]


def test_retry_failed_companies_resolves_what_now_answers(retry_world, caplog):
    conn, writes = retry_world
    with caplog.at_level(logging.INFO, logger=pipeline.logger.name):
        pipeline.retry_failed_companies()
    assert writes == EXPECTED_WRITES
    assert [f["company_code"] for f in storage.get_unresolved_failures(conn)] == ["DEAD"]
    msgs = [r.getMessage() for r in caplog.records]
    assert "Retrying DEAD..." in msgs and "Retry failed for DEAD" in msgs
    assert "Successfully fetched OKAY" in msgs


def test_retry_failures_nested_logs_as_a_pipeline_step(retry_world, caplog):
    conn, writes = retry_world
    with caplog.at_level(logging.INFO, logger=pipeline.logger.name):
        pipeline._retry_failures(conn, storage.get_unresolved_failures(conn), nested=True)
    assert writes == EXPECTED_WRITES
    assert [f["company_code"] for f in storage.get_unresolved_failures(conn)] == ["DEAD"]
    msgs = [r.getMessage() for r in caplog.records]
    assert "  Retrying OKAY..." in msgs and "  Retry still failed for DEAD" in msgs
    assert not any("Successfully" in m for m in msgs)


# ── The ledger settles only what was fetched *and* stored ────────────────────

COMPANY, CASH = "GetListedSupplementCompany", "GetListedCashDividends"


def _open(conn) -> set:
    return {(f["company_code"], f["endpoint"]) for f in storage.get_unresolved_failures(conn)}


def test_a_retry_that_dies_storing_leaves_the_failure_open(retry_world, monkeypatch):
    """The row used to be marked resolved before the actions were stored, so a
    store that raised left a company marked fetched with nothing written."""
    conn, _ = retry_world

    def broken(c, df):
        raise RuntimeError("disk full")
    monkeypatch.setattr(pipeline.storage, "upsert_stock_actions", broken)
    with pytest.raises(RuntimeError):
        pipeline._retry_failures(conn, storage.get_unresolved_failures(conn))
    assert _open(conn) == {("DEAD", COMPANY), ("OKAY", COMPANY)}


def test_a_retry_whose_cash_pages_fail_again_keeps_only_that_debt(retry_world, monkeypatch):
    conn, _ = retry_world
    storage.record_fetch_failure(conn, "OKAY", CASH, "page 2: timeout")
    asked = []

    def partial(name, conn=None, company_code=None):
        asked.append(company_code)
        storage.record_fetch_failure(conn, company_code, CASH, "page 2: timeout")
        return [{"c": 1}], False
    monkeypatch.setattr(pipeline.b3_corporate_actions, "fetch_cash_dividends_paginated",
                        partial)
    pipeline._retry_failures(conn, storage.get_unresolved_failures(conn))
    assert asked == ["OKAY"], "one refetch per company, not one per owed endpoint"
    assert _open(conn) == {("DEAD", COMPANY), ("OKAY", CASH)}


def test_a_failed_cash_page_is_recorded_with_what_came_before_it(monkeypatch):
    """It used to `break` with the pages it had and say nothing: a company's
    dividend history silently cut short, and never asked for again."""
    import requests

    conn = _in_memory_conn()
    storage.init_db(conn, rebuild=False)
    b3 = pipeline.b3_corporate_actions
    pages = iter([{"page": {"totalPages": 2}, "results": [{"r": 1}]},
                  requests.ConnectionError("reset")])

    class _Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            if isinstance(self.body, Exception):
                raise self.body

        def json(self):
            return self.body
    monkeypatch.setattr(b3.requests, "get", lambda *a, **k: _Resp(next(pages)))
    monkeypatch.setattr(b3.time, "sleep", lambda s: None)

    records, complete = b3.fetch_cash_dividends_paginated(
        "PETROBRAS", conn=conn, company_code="PETR")
    assert (records, complete) == ([{"r": 1}], False)
    assert _open(conn) == {("PETR", CASH)}


def test_a_full_step6_fetch_pays_an_open_failure_once_stored(tmp_path, monkeypatch):
    """Step 6 refetches every company. A success there used to leave an earlier
    failure open, so step 9b fetched the same company a second time that run."""
    db = tmp_path / "step6.sqlite"
    monkeypatch.setattr(storage, "get_connection", lambda: sqlite3.connect(db))
    conn = sqlite3.connect(db)
    storage.init_db(conn, rebuild=False)
    for code, endpoint in (("PETR", COMPANY), ("PETR", CASH), ("VALE", CASH)):
        storage.record_fetch_failure(conn, code, endpoint, "last night")
    b3 = pipeline.b3_corporate_actions
    monkeypatch.setattr(b3, "fetch_company_data",
                        lambda code, conn=None: {"tradingName": code, "stockDividends": []})
    monkeypatch.setattr(b3, "fetch_cash_dividends_paginated",
                        lambda name, conn=None, company_code=None: ([], name == "PETR"))
    monkeypatch.setattr(b3.time, "sleep", lambda s: None)

    paid: dict = {}
    b3.fetch_all_corporate_actions(["PETR", "VALE"], {}, fetched=paid)
    assert paid == {"PETR": (COMPANY, CASH), "VALE": (COMPANY,)}
    assert len(_open(conn)) == 3, "nothing is resolved before the store"
    pipeline._resolve_paid(conn, paid)
    assert _open(conn) == {("VALE", CASH)}
