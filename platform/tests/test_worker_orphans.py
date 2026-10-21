"""A worker that boots closes the runs its dead predecessor left `running`.

Every deploy recreates the worker and ingest containers, and anything still in
flight past stop_grace_period is SIGKILLed with its row stuck at `running`.
Nothing retries it, and for a backtest that row counts against the tenant's
quota for good and cannot be purged. These run the real sweep against the test
database, because what matters is the SQL: which table, which statuses, and that
each role only touches its own.
"""
import uuid

import pytest
from platform_app import worker
from platform_app.db import async_session_maker
from platform_app.models import BacktestRun, IngestRun
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed() -> dict[str, uuid.UUID]:
    tid = uuid.uuid4()
    async with async_session_maker() as session:
        rows = {
            status: BacktestRun(tenant_id=tid, strategy="MomSharpe",
                                status=status, params={})
            for status in ("pending", "running", "done")
        }
        rows["ingest"] = IngestRun(kind="prices", status="running")
        session.add_all(rows.values())
        await session.commit()
        return {k: r.id for k, r in rows.items()}


async def _state(ids):
    async with async_session_maker() as session:
        runs = {r.id: r for r in await session.scalars(
            select(BacktestRun).where(BacktestRun.id.in_(
                [ids["pending"], ids["running"], ids["done"]])))}
        ingest = await session.get(IngestRun, ids["ingest"])
        return runs, ingest


async def _boot(monkeypatch, queues: str | None):
    """worker._run up to the point it would start taking jobs."""
    async def no_jobs(**_kwargs):
        return None

    monkeypatch.setattr(worker.app, "run_worker_async", no_jobs)
    if queues is None:
        monkeypatch.delenv("WORKER_QUEUES", raising=False)
    else:
        monkeypatch.setenv("WORKER_QUEUES", queues)
    await worker._run()


async def test_the_default_worker_fails_its_orphaned_backtests_only(monkeypatch):
    ids = await _seed()

    await _boot(monkeypatch, None)

    runs, ingest = await _state(ids)
    orphan = runs[ids["running"]]
    assert orphan.status == "error"
    assert orphan.error == worker.INTERRUPTED_BACKTEST
    assert orphan.finished_at is not None
    # Queued work is still queued — its procrastinate job will run — and
    # finished work keeps its result.
    assert runs[ids["pending"]].status == "pending"
    assert runs[ids["done"]].status == "done"
    # The ingest worker may be mid-run while this one restarts.
    assert ingest.status == "running"


async def test_the_ingest_worker_fails_its_orphaned_runs_only(monkeypatch):
    ids = await _seed()

    await _boot(monkeypatch, "ingest")

    runs, ingest = await _state(ids)
    assert ingest.status == "error"
    assert ingest.error == worker.INTERRUPTED_INGEST
    assert ingest.finished_at is not None
    assert runs[ids["running"]].status == "running"


async def test_a_closed_orphan_stops_counting_against_the_quota(monkeypatch):
    """The point of the sweep: the 429 a dead run caused goes away on its own."""
    from platform_app.models import User
    from platform_app.routes import inflight_backtests

    ids = await _seed()
    async with async_session_maker() as session:
        tid = (await session.get(BacktestRun, ids["running"])).tenant_id
    user = User(id=tid, email=f"{tid}@example.com", hashed_password="x")

    async with async_session_maker() as session:
        assert await inflight_backtests(session, user) == 2   # pending + running
    await _boot(monkeypatch, None)
    async with async_session_maker() as session:
        assert await inflight_backtests(session, user) == 1   # pending only
