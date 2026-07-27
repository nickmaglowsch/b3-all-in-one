"""GET /market/status must not read the whole ingest_runs table.

`_last_ingest` selected every row ordered by (kind, started_at DESC) and picked
the first per kind in Python. The table grows by one row per ingestion attempt
forever, and the endpoint is polled on a timer from every open tab — so the cost
of answering "is the plane fresh?" grew with how often the plane had been
refreshed, and was deliberately left out of the payload cache.

The index for it already existed (ix_ingest_runs_kind_started); only the query
was wrong.
"""
import datetime

import pytest
from sqlalchemy import text

from platform_app.db import async_session_maker
from platform_app.models import IngestRun


async def _clear():
    async with async_session_maker() as s:
        await s.execute(text("DELETE FROM ingest_runs"))
        await s.commit()


async def _seed(n_per_kind: int = 30):
    """A history deep enough that reading all of it is visibly different from
    reading the newest per kind."""
    base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    async with async_session_maker() as s:
        for kind in ("prices", "fundamentals"):
            for i in range(n_per_kind):
                s.add(IngestRun(
                    kind=kind,
                    status="done" if i < n_per_kind - 1 else "error",
                    started_at=base + datetime.timedelta(hours=i),
                    finished_at=base + datetime.timedelta(hours=i, minutes=5),
                    error=None if i < n_per_kind - 1 else "boom",
                ))
        await s.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_reports_the_latest_run_per_kind():
    from platform_app.routes import _last_ingest

    await _clear()
    await _seed()
    async with async_session_maker() as s:
        out = await _last_ingest(s)

    assert set(out) == {"prices", "fundamentals"}
    for kind in ("prices", "fundamentals"):
        # The newest row is the one seeded last, which is the error.
        assert out[kind]["status"] == "error", kind
        assert out[kind]["started_at"].startswith("2026-01-02T05:00")
    await _clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_never_exposes_the_error_text():
    """It carries subprocess output with server paths — status is the part a
    caller needs (same reasoning as privacy._INTERNAL_COLUMNS)."""
    from platform_app.routes import _last_ingest

    await _clear()
    await _seed()
    async with async_session_maker() as s:
        out = await _last_ingest(s)
    assert all("error" not in v for v in out.values())
    await _clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_reads_only_the_newest_row_per_kind():
    """The regression guard. 60 rows in the table; the database must hand back
    two, not sixty for Python to filter.

    Asserted on the emitted SQL because that is precisely what changed — an
    unbounded `SELECT ... ORDER BY kind, started_at DESC` still produces the
    right answer, which is why this went unnoticed.
    """
    from sqlalchemy import event

    from platform_app.db import engine
    from platform_app.routes import _last_ingest

    await _clear()
    await _seed(n_per_kind=30)

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "ingest_runs" in statement:
            statements.append(" ".join(statement.split()).upper())

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with async_session_maker() as s:
            out = await _last_ingest(s)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)

    assert len(out) == 2
    assert statements, "no query against ingest_runs was captured"
    sql = statements[-1]
    assert "DISTINCT ON" in sql, f"still an unbounded read of ingest_runs:\n{sql}"
    await _clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_table_is_not_an_error():
    from platform_app.routes import _last_ingest

    await _clear()
    async with async_session_maker() as s:
        assert await _last_ingest(s) == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_market_status_still_reports_last_ingest(client):
    await _clear()
    await _seed(n_per_kind=3)
    r = await client.get("/market/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body["last_ingest"]) == {"prices", "fundamentals"}
    assert "stale" in body and "prices" in body
    await _clear()
