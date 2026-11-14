"""A worker that boots closes the runs its dead predecessor left `running`,
fails the backtests lost while `pending`, and re-queues imports still in flight.

Every deploy recreates the worker and ingest containers, and anything still in
flight past stop_grace_period is SIGKILLed with its row stuck at `running`.
Nothing retries it, and for a backtest that row counts against the tenant's
quota for good and cannot be purged. These run the real sweep against the test
database, because what matters is the SQL: which table, which statuses, and that
each role only touches its own.
"""
import datetime
import uuid

import pytest
from platform_app import tasks, worker
from platform_app.db import async_session_maker
from platform_app.models import BacktestRun, Import, IngestRun
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

_AN_HOUR_AGO = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)


async def _seed() -> dict[str, uuid.UUID]:
    """One backtest per state, plus two `pending` ones from an hour ago: `lost`
    has no procrastinate job, `queued` has one waiting."""
    tid = uuid.uuid4()
    async with async_session_maker() as session:
        rows = {
            status: BacktestRun(tenant_id=tid, strategy="MomSharpe",
                                status=status, params={})
            for status in ("pending", "running", "done")
        }
        for key in ("lost", "queued"):
            rows[key] = BacktestRun(tenant_id=tid, strategy="MomSharpe", status="pending",
                                    params={}, created_at=_AN_HOUR_AGO)
        rows["ingest"] = IngestRun(kind="prices", status="running")
        session.add_all(rows.values())
        await session.commit()
        ids = {k: r.id for k, r in rows.items()}
    async with tasks.app.open_async():
        await tasks.run_backtest.defer_async(run_id=str(ids["queued"]))
    return ids


async def _state(ids):
    async with async_session_maker() as session:
        runs = {r.id: r for r in await session.scalars(
            select(BacktestRun).where(BacktestRun.id.in_(
                [v for k, v in ids.items() if k != "ingest"])))}
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
    # This used to assert that every pending row stays pending. Not any more: one
    # whose job is gone — its defer never happened, or a dead worker took it —
    # had no way out at all, so boot fails it too. What stays pending is work
    # that can still run: a row with a job waiting, and a row younger than
    # LOST_GRACE, which may be a submission between its commit and its defer.
    lost = runs[ids["lost"]]
    assert (lost.status, lost.error) == ("error", worker.LOST_BACKTEST)
    assert lost.finished_at is not None
    assert runs[ids["queued"]].status == "pending"
    assert runs[ids["pending"]].status == "pending"
    # Finished work keeps its result.
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
    assert runs[ids["lost"]].status == "pending"


async def test_a_closed_orphan_stops_counting_against_the_quota(monkeypatch):
    """The point of the sweep: the 429 a dead run caused goes away on its own."""
    from platform_app.models import User
    from platform_app.routes import inflight_backtests

    ids = await _seed()
    async with async_session_maker() as session:
        tid = (await session.get(BacktestRun, ids["running"])).tenant_id
    user = User(id=tid, email=f"{tid}@example.com", hashed_password="x")

    async with async_session_maker() as session:
        assert await inflight_backtests(session, user) == 4   # 3 pending + running
    await _boot(monkeypatch, None)
    async with async_session_maker() as session:
        assert await inflight_backtests(session, user) == 2   # the two that can run


async def test_the_default_worker_releases_its_dead_predecessors_import_lock(monkeypatch):
    """A process_imports SIGKILLed mid-run stays `doing` and keeps its lock, so
    every later upload of that tenant would wait behind it forever. Boot fails
    it; the ingest worker's job on its own queue is left alone."""
    tid = uuid.uuid4()
    async with tasks.app.open_async():
        c = tasks.app.connector
        await tasks.process_imports.configure(lock=tasks.imports_lock(tid)).defer_async(
            tenant_id=str(tid))
        await tasks.process_imports.configure(queue="ingest").defer_async(tenant_id=str(tid))
        dead = (await c.execute_query_one_async(
            "INSERT INTO procrastinate_workers (last_heartbeat)"
            " VALUES (now() - interval '1 hour') RETURNING id"))["id"]
        await c.execute_query_async(
            "UPDATE procrastinate_jobs SET status = 'doing', worker_id = %(w)s"
            " WHERE args->>'tenant_id' = %(t)s", w=dead, t=str(tid))

    await _boot(monkeypatch, None)

    async with tasks.app.open_async():
        jobs = await tasks.app.connector.execute_query_all_async(
            "SELECT queue_name, status FROM procrastinate_jobs"
            " WHERE args->>'tenant_id' = %(t)s ORDER BY queue_name", t=str(tid))
    assert jobs == [{"queue_name": "default", "status": "failed"},
                    {"queue_name": "ingest", "status": "doing"}]


async def test_the_default_worker_requeues_imports_still_in_flight(monkeypatch):
    """An import has no `running` row to sweep: whatever lost its job — a defer
    that never happened, a job failed above, a batch a superseded job left
    `processing` — another job fixes. One per tenant, never a second beside one
    already waiting (the queueing lock), and none for a book that is settled."""
    stuck, waiting, settled = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        for tid, statuses in ((stuck, ("processing", "pending")), (waiting, ("pending",)),
                              (settled, ("done", "error"))):
            session.add_all(Import(tenant_id=tid, filename=f"{s}.xlsx", stored_path="/x",
                                   kind="negociacao", status=s) for s in statuses)
        await session.commit()
    async with tasks.app.open_async():
        await tasks.defer_process_imports(waiting)

    await _boot(monkeypatch, None)

    async with tasks.app.open_async():
        jobs = await tasks.app.connector.execute_query_all_async(
            "SELECT args->>'tenant_id' AS tid, lock, queueing_lock FROM procrastinate_jobs"
            " WHERE task_name = 'process_imports' AND status = 'todo'"
            " AND args->>'tenant_id' = ANY(%(t)s)",
            t=[str(stuck), str(waiting), str(settled)])
    assert sorted(j["tid"] for j in jobs) == sorted([str(stuck), str(waiting)])
    assert all(j["lock"] == j["queueing_lock"] == f"imports:{j['tid']}" for j in jobs)
