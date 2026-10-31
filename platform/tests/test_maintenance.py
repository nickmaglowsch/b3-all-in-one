"""The after-a-fix recomputes: `python -m platform_app.maintenance ...`.

Both tasks run against the real test database and the real procrastinate tables:
what matters is which rows they pick, what lands in the queue, and — for the
backtests — that a tenant never ends up worse off than before. The engine is the
only thing stubbed, the way test_backtest_failure_paths does it; the backtest
"worker" is the real tasks.run_backtest, run in-process when the job is deferred.
"""
import asyncio
import datetime
import json
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from platform_app import enginerun, maintenance, tasks
from platform_app import maintenance_backtests as mb
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import BacktestRun, Import

pytestmark = pytest.mark.asyncio(loop_scope="session")

OLD_METRICS = {"strategy": "MomSharpe", "pretax": {"Sharpe": 1.9}}
OLD_FINISHED = datetime.datetime(2026, 3, 1, 12, tzinfo=datetime.UTC)


# ── reprocess-imports ─────────────────────────────────────────────────────────

async def _seed_imports(tmp_path, tid, statuses, missing=False):
    async with async_session_maker() as session:
        for i, status in enumerate(statuses):
            path = tmp_path / f"{tid}-{i}.xlsx"
            if not missing:
                path.write_bytes(b"x")
            session.add(Import(tenant_id=tid, filename=path.name, stored_path=str(path),
                               kind="negociacao", status=status))
        await session.commit()


async def _import_jobs(tid) -> list[dict]:
    async with tasks.app.open_async():
        return await tasks.app.connector.execute_query_all_async(
            "SELECT status, priority FROM procrastinate_jobs WHERE task_name ="
            " 'process_imports' AND args->>'tenant_id' = %(t)s", t=str(tid))


async def test_reprocess_imports_defers_once_per_tenant(tmp_path):
    a, b, nobody = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_imports(tmp_path, a, ["done", "done", "processing"])
    await _seed_imports(tmp_path, b, ["error"])

    summary = await maintenance.reprocess_imports(tenants=[a, b, nobody])

    assert summary == {"tenants_found": 2, "tenants_selected": 2, "deferred": 2}
    for tid in (a, b):
        # One job for the whole book, behind anything a user queues.
        assert await _import_jobs(tid) == [
            {"status": "todo", "priority": maintenance.MAINTENANCE_PRIORITY}]
    assert await _import_jobs(nobody) == []
    # Statuses are the task's to move, not ours.
    async with async_session_maker() as session:
        got = sorted(await session.scalars(select(Import.status).where(Import.tenant_id == a)))
    assert got == ["done", "done", "processing"]

    # A second pass finds both still queued and adds nothing.
    again = await maintenance.reprocess_imports(tenants=[a, b])
    assert again["skipped_already_queued"] == 2 and "deferred" not in again
    assert len(await _import_jobs(a)) == 1


async def test_reprocess_imports_job_shares_the_upload_lock(tmp_path):
    """Unlocked, the reprocess job could run beside the tenant's own upload job
    and take both worker slots: it carries the same locks an upload's does."""
    tid = uuid.uuid4()
    await _seed_imports(tmp_path, tid, ["done"])
    await maintenance.reprocess_imports(tenants=[tid])
    async with tasks.app.open_async():
        jobs = await tasks.app.connector.execute_query_all_async(
            "SELECT lock, queueing_lock FROM procrastinate_jobs WHERE task_name ="
            " 'process_imports' AND args->>'tenant_id' = %(t)s", t=str(tid))
        # An upload now finds the waiting job instead of queueing a second.
        await tasks.defer_process_imports(tid)
    key = tasks.imports_lock(tid)
    assert jobs == [{"lock": key, "queueing_lock": key}]
    assert len(await _import_jobs(tid)) == 1


async def test_reprocess_imports_dry_run_and_limit_defer_nothing_extra(tmp_path):
    a, b = uuid.uuid4(), uuid.uuid4()
    await _seed_imports(tmp_path, a, ["done"])
    await _seed_imports(tmp_path, b, ["done"])

    dry = await maintenance.reprocess_imports(dry_run=True, tenants=[a, b])
    assert dry["would_defer"] == 2
    assert await _import_jobs(a) == [] and await _import_jobs(b) == []

    one = await maintenance.reprocess_imports(limit=1, tenants=[a, b])
    assert one["deferred"] == 1 and one["tenants_selected"] == 1
    assert len(await _import_jobs(a)) + len(await _import_jobs(b)) == 1


async def test_reprocess_imports_skips_a_book_whose_extract_is_gone(tmp_path, capsys):
    tid = uuid.uuid4()
    await _seed_imports(tmp_path, tid, ["done"], missing=True)
    summary = await maintenance.reprocess_imports(tenants=[tid])
    assert summary["skipped_missing_files"] == 1
    assert await _import_jobs(tid) == []
    assert "missing on disk" in capsys.readouterr().out


async def test_cli_dispatches_with_the_parsed_flags(tmp_path):
    tid = uuid.uuid4()
    await _seed_imports(tmp_path, tid, ["done"])
    args = maintenance._parser().parse_args(
        ["reprocess-imports", "--dry-run", "--tenant", str(tid)])
    assert (await maintenance._run(args))["would_defer"] == 1
    with pytest.raises(SystemExit):
        maintenance._parser().parse_args(["rerun-backtests", "--limit", "0"])
    with pytest.raises(SystemExit):
        maintenance._parser().parse_args(["drop-everything"])


# ── rerun-backtests ───────────────────────────────────────────────────────────

@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(mb, "POLL_S", 0.01)
    return tmp_path / "artifacts"


async def _seed_run(tid, *, status="done", metrics=OLD_METRICS, files=True) -> uuid.UUID:
    run = BacktestRun(tenant_id=tid, strategy="MomSharpe", status=status, params={},
                      metrics=metrics, finished_at=OLD_FINISHED)
    async with async_session_maker() as session:
        session.add(run)
        await session.commit()
    if files:
        d = mb.artifacts_dir(run.id)
        d.mkdir(parents=True)
        (d / "curves.csv").write_text("old curves")
        (d / "metrics.json").write_text("old metrics")
        async with async_session_maker() as session:
            (await session.get(BacktestRun, run.id)).artifacts_path = str(d)
            await session.commit()
    return run.id


async def _row(rid):
    async with async_session_maker() as session:
        return await session.get(BacktestRun, rid)


class _Worker:
    """Stands in for procrastinate + the worker: a deferred run_backtest runs the
    real task in-process. Records the order, and how many of the tenants' runs
    were pending/running at each defer — which must always be exactly one."""

    def __init__(self, tenants, fail=(), on_defer=None):
        self.tenants, self.fail, self.on_defer = tenants, set(fail), on_defer
        self.order, self.inflight, self.jobs = [], [], []
        self.real = tasks.run_backtest

    async def defer_async(self, run_id):
        async with async_session_maker() as session:
            self.inflight.append(await session.scalar(
                select(func.count()).select_from(BacktestRun).where(
                    BacktestRun.tenant_id.in_(self.tenants),
                    BacktestRun.status.in_(("pending", "running")))))
        assert all(j.done() for j in self.jobs), "a second run was deferred mid-flight"
        self.order.append(uuid.UUID(run_id))
        if self.on_defer:
            await self.on_defer(uuid.UUID(run_id))
        self.jobs.append(asyncio.get_running_loop().create_task(self.real(run_id=run_id)))

    def engine(self, script, args, extra_env=None, stdin=None):
        out_path = Path(args[1])
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "curves.csv").write_text("new curves")
        if out_path.name in self.fail:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="engine blew up")
        (out_path / "metrics.json").write_text("new metrics")
        summary = {"strategy": "MomSharpe", "sharpe_risk_free": "CDI",
                   "pretax": {"Sharpe": 0.4}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(summary), stderr="")


def _install(monkeypatch, worker):
    monkeypatch.setattr(tasks, "run_backtest", worker)
    monkeypatch.setattr(enginerun, "run_script", worker.engine)


async def test_rerun_selects_only_done_runs_missing_the_key_one_at_a_time(
        artifacts, monkeypatch):
    tid, other = uuid.uuid4(), uuid.uuid4()
    stale1 = await _seed_run(tid)
    fresh = await _seed_run(tid, metrics={**OLD_METRICS, "sharpe_risk_free": "CDI"})
    errored = await _seed_run(tid, status="error")
    queued = await _seed_run(tid, status="pending", files=False)
    stale2 = await _seed_run(tid, metrics=None)
    elsewhere = await _seed_run(other)
    worker = _Worker([tid, other])
    _install(monkeypatch, worker)

    summary = await mb.rerun_backtests(tenants=[tid])

    assert worker.order == [stale1, stale2]
    # Only ours was in flight when each one was deferred: the user's own
    # pending run plus this one, never two of ours.
    assert worker.inflight == [2, 2]
    assert summary["ok"] == 2 and summary["remaining"] == 0
    for rid in (stale1, stale2):
        row = await _row(rid)
        assert (row.status, row.error, row.metrics["sharpe_risk_free"]) == ("done", None, "CDI")
        assert row.finished_at > OLD_FINISHED
        assert (mb.artifacts_dir(rid) / "metrics.json").read_text() == "new metrics"
        assert not mb._backup_dir(rid).exists()
    # Everything else is exactly as it was — another tenant's stale run included.
    for rid, status in ((fresh, "done"), (errored, "error"), (queued, "pending"),
                        (elsewhere, "done")):
        row = await _row(rid)
        assert (row.status, row.finished_at) == (status, OLD_FINISHED)
    assert (await _row(elsewhere)).metrics == OLD_METRICS
    assert (mb.artifacts_dir(elsewhere) / "metrics.json").read_text() == "old metrics"


async def test_a_failed_rerun_gives_the_tenant_back_the_old_result(artifacts, monkeypatch):
    tid = uuid.uuid4()
    bad = await _seed_run(tid)
    good = await _seed_run(tid)
    worker = _Worker([tid], fail={str(bad)})
    _install(monkeypatch, worker)

    summary = await mb.rerun_backtests(tenants=[tid])

    assert (summary["ok"], summary["failed"]) == (1, 1)
    row = await _row(bad)
    assert (row.status, row.error, row.metrics) == ("done", None, OLD_METRICS)
    assert row.finished_at == OLD_FINISHED
    # The engine rewrote curves.csv before it failed; the backup's goes back.
    assert (mb.artifacts_dir(bad) / "curves.csv").read_text() == "old curves"
    assert not mb._backup_dir(bad).exists()
    assert (await _row(good)).metrics["sharpe_risk_free"] == "CDI"

    # Remembered: the next session does not spend its time on it again...
    worker.order.clear()
    assert (await mb.rerun_backtests(tenants=[tid]))["selected"] == 0
    # ...unless asked to.
    again = await mb.rerun_backtests(tenants=[tid], retry_failed=True)
    assert again["failed"] == 1 and worker.order == [bad]


async def test_a_run_the_queue_never_reaches_is_cancelled_and_restored(artifacts):
    """Real procrastinate defer, and no worker in the test: the job sits in
    `todo` until the queue timeout, is cancelled, and the row goes back."""
    tid = uuid.uuid4()
    rid = await _seed_run(tid)

    summary = await mb.rerun_backtests(tenants=[tid], queue_timeout=0.2)

    assert summary["failed"] == 1
    assert (await _row(rid)).status == "done"
    async with tasks.app.open_async():
        job = await tasks.app.connector.execute_query_one_async(
            "SELECT status FROM procrastinate_jobs WHERE task_name = 'run_backtest'"
            " AND args->>'run_id' = %(r)s", r=str(rid))
    assert job["status"] == "cancelled"


async def test_a_run_whose_job_vanished_is_restored(artifacts, monkeypatch):
    class _Nowhere:
        async def defer_async(self, run_id):
            return None

    tid = uuid.uuid4()
    rid = await _seed_run(tid)
    monkeypatch.setattr(tasks, "run_backtest", _Nowhere())
    summary = await mb.rerun_backtests(tenants=[tid], queue_timeout=0.05)
    assert summary["failed"] == 1
    assert (await _row(rid)).metrics == OLD_METRICS


async def test_a_run_deleted_mid_rerun_leaves_no_files_behind(artifacts, monkeypatch):
    async def delete_it(rid):
        async with async_session_maker() as session:
            await session.execute(delete(BacktestRun).where(BacktestRun.id == rid))
            await session.commit()

    tid = uuid.uuid4()
    rid = await _seed_run(tid)
    _install(monkeypatch, _Worker([tid], on_defer=delete_it))
    summary = await mb.rerun_backtests(tenants=[tid])
    assert summary["deleted"] == 1 and "failed" not in summary
    assert not mb.artifacts_dir(rid).exists() and not mb._backup_dir(rid).exists()


async def test_dry_run_counts_per_tenant_and_changes_nothing(artifacts, monkeypatch, capsys):
    class _NoDefer:
        async def defer_async(self, **kw):
            raise AssertionError("dry run deferred a job")

    a, b = uuid.uuid4(), uuid.uuid4()
    for tid, n in ((a, 2), (b, 1)):
        for _ in range(n):
            await _seed_run(tid)
    monkeypatch.setattr(tasks, "run_backtest", _NoDefer())

    summary = await mb.rerun_backtests(dry_run=True, tenants=[a, b])

    assert (summary["qualifying"], summary["tenants"]) == (3, 2)
    out = capsys.readouterr().out
    assert f"tenant={a} qualifying=2" in out and f"tenant={b} qualifying=1" in out
    assert not (artifacts / "_maintenance").exists()


async def test_limit_and_budget_bound_a_session(artifacts, monkeypatch):
    tid = uuid.uuid4()
    for _ in range(3):
        await _seed_run(tid)
    worker = _Worker([tid])
    _install(monkeypatch, worker)

    assert (await mb.rerun_backtests(tenants=[tid], limit=1))["remaining"] == 2
    spent = await mb.rerun_backtests(tenants=[tid], budget_s=1e-9)
    assert "ok" not in spent and spent["remaining"] == 2
    assert len(worker.order) == 1


async def test_a_killed_session_is_settled_by_the_next_one(artifacts, monkeypatch):
    tid = uuid.uuid4()
    # Killed after the reset, before the defer: pending, no job anywhere.
    orphan = await _seed_run(tid)
    assert await mb._backup(orphan) and await mb._reset(orphan)
    # Killed after its run failed, before the restore.
    failed = await _seed_run(tid)
    assert await mb._backup(failed) and await mb._reset(failed)
    async with async_session_maker() as session:
        (await session.get(BacktestRun, failed)).status = "error"
        await session.commit()
    # Killed mid-backup: no row.json, the row was never touched.
    (mb._backup_dir(uuid.uuid4())).mkdir(parents=True)
    worker = _Worker([tid])
    _install(monkeypatch, worker)

    summary = await mb.rerun_backtests(tenants=[tid])

    assert summary["recovered_ok"] == 1 and summary["recovered_failed"] == 1
    assert (await _row(orphan)).metrics["sharpe_risk_free"] == "CDI"
    # The restored one qualifies again and is retried in the same session.
    assert worker.order == [orphan, failed]
    assert (await _row(failed)).metrics["sharpe_risk_free"] == "CDI"
    assert list((artifacts / "_maintenance" / "backup").iterdir()) == []


async def test_a_job_held_by_a_dead_worker_does_not_hang_the_session(artifacts, monkeypatch):
    """Fetched (`doing`) but never started: the row stays pending. One extra
    window for a worker picking it up right now, then give up and restore."""
    real_defer = mb._defer

    async def defer_then_hold(rid):
        await real_defer(rid)
        await tasks.app.connector.execute_query_all_async(
            "UPDATE procrastinate_jobs SET status = 'doing' WHERE task_name ="
            " 'run_backtest' AND args->>'run_id' = %(r)s RETURNING id", r=str(rid))

    tid = uuid.uuid4()
    rid = await _seed_run(tid)
    monkeypatch.setattr(mb, "_defer", defer_then_hold)
    summary = await mb.rerun_backtests(tenants=[tid], queue_timeout=0.05)
    assert summary["failed"] == 1
    assert (await _row(rid)).status == "done"
    assert str(rid) in mb._ledger().read_text()


async def test_cli_exit_code_is_red_when_a_run_was_restored(monkeypatch):
    async def fake_run(args):
        return {"failed": 1} if args.task == "rerun-backtests" else {"deferred": 3}

    monkeypatch.setattr(maintenance, "_run", fake_run)
    # main() owns its event loop (asyncio.run), so it cannot share this one.
    assert await asyncio.to_thread(maintenance.main, ["rerun-backtests"]) == 1
    assert await asyncio.to_thread(maintenance.main, ["reprocess-imports"]) == 0
