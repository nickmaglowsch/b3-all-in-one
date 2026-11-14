"""`rerun-backtests`: recompute finished backtests whose numbers predate a fix.

The selection is every `done` run whose metrics lack ``sharpe_risk_free`` — the
key the engine has written into every summary since Sharpe became excess over the
CDI (#71). A run computed before that carries a Sharpe over zero, longs-only short
handling, raw-quarter fundamentals and the old CAGR, and nothing ever revisits it.

Each run is recomputed IN PLACE, under its own id, and strictly one at a time:

  1. back up — copy ARTIFACTS_DIR/{id} and the row's result columns into
     ARTIFACTS_DIR/_maintenance/backup/{id}/ (row.json written last, so its
     presence means the backup is whole);
  2. reset — `done` → `pending`, conditional on the row still qualifying;
  3. defer `tasks.run_backtest(run_id)` — the very task a submission runs. On a
     row that already finished it re-runs the engine into the same directory
     (metrics.json and curves.csv are rewritten) and on success overwrites every
     result column: status, error, metrics, artifacts_path, finished_at;
  4. poll the row until it leaves pending/running;
  5. settle — `done` with the new key: drop the backup. Anything else (error, a
     timeout, a job the queue never got to): put the backup's artifacts and
     columns back, so the tenant keeps the result they had, and log it failed.

One at a time because BACKTEST_QUOTA counts an account's pending/running runs
(routes.inflight_backtests): a bulk requeue would 429 every bench user whose old
runs were recent, and would sit in front of everyone's new submissions for hours.

Restartable. A killed session leaves at most one run mid-flight, and its backup
says so: the next session settles leftover backups before selecting anything —
waiting for a run still in flight, re-deferring one reset but never queued —
and the selection skips runs that already carry the key. A run that failed is
recorded in ARTIFACTS_DIR/_maintenance/rerun-backtests-failed.txt and skipped by
later sessions (`--retry-failed` to try again), so a deterministic failure — a
user strategy since deleted, say — does not eat every `--limit` forever.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path

from sqlalchemy import func, not_, null, or_, select

from platform_app import tasks
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.jobstatus import (
    BACKTEST_FINISHED, BACKTEST_INFLIGHT, BacktestStatus, transition,
)
from platform_app.models import BacktestRun
from platform_app.routes_backtest_retention import artifacts_dir

KEY = "sharpe_risk_free"
# Past SUBPROCESS_TIMEOUT_S the engine is killed and run_backtest writes `error`
# within seconds; a run still `running` this long after is a dead worker's.
RUN_MARGIN_S = 300
POLL_S = 2.0
_COLUMNS = ("status", "error", "metrics", "artifacts_path")


def log(msg: str) -> None:
    # print, not logging: nothing configures logging in this process, and this
    # is read off the Actions log of the maintenance workflow.
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[maintenance {now}] {msg}", flush=True)


def _root() -> Path:
    return Path(settings.artifacts_dir).resolve() / "_maintenance"


def _backup_dir(rid: uuid.UUID | str) -> Path:
    return _root() / "backup" / str(rid)


def _ledger() -> Path:
    return _root() / "rerun-backtests-failed.txt"


def _failed_before() -> set[str]:
    try:
        return set(_ledger().read_text().split())
    except FileNotFoundError:
        return set()


def _record_failure(rid: uuid.UUID) -> None:
    _ledger().parent.mkdir(parents=True, exist_ok=True)
    with _ledger().open("a") as f:
        f.write(f"{rid}\n")


def _stale():
    return or_(BacktestRun.metrics.is_(None), not_(BacktestRun.metrics.has_key(KEY)))


def _qualifying(tenants):
    q = select(BacktestRun.id, BacktestRun.tenant_id, BacktestRun.strategy).where(
        BacktestRun.status == BacktestStatus.DONE, _stale())
    if tenants:
        q = q.where(BacktestRun.tenant_id.in_(tenants))
    return q.order_by(BacktestRun.created_at, BacktestRun.id)


async def _rows(q) -> list:
    async with async_session_maker() as session:
        return list((await session.execute(q)).all())


async def _state(rid: uuid.UUID) -> tuple[str | None, bool, str | None]:
    """(status, metrics carry KEY, error) — status None when the row is gone."""
    async with async_session_maker() as session:
        run = await session.get(BacktestRun, rid)
        if run is None:
            return None, False, None
        return run.status, isinstance(run.metrics, dict) and KEY in run.metrics, run.error


# ── backup / reset / restore ──────────────────────────────────────────────────

async def _backup(rid: uuid.UUID) -> bool:
    """Copy the run's artifacts and result columns aside. False if the run no
    longer qualifies (deleted, or finished elsewhere) — nothing is written."""
    async with async_session_maker() as session:
        run = await session.get(BacktestRun, rid)
        if run is None or run.status != BacktestStatus.DONE:
            return False
        snap = {c: getattr(run, c) for c in _COLUMNS}
        snap["finished_at"] = run.finished_at.isoformat() if run.finished_at else None

    def _write() -> None:
        dest = _backup_dir(rid)
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        if artifacts_dir(rid).is_dir():
            shutil.copytree(artifacts_dir(rid), dest / "artifacts")
        (dest / "row.json").write_text(json.dumps(snap))  # last: marks it whole

    await asyncio.to_thread(_write)
    return True


async def _reset(rid: uuid.UUID) -> bool:
    """done → pending, only if the row still qualifies. Metrics and
    artifacts_path stay until the new run overwrites them."""
    async with async_session_maker() as session:
        changed = await transition(
            session, BacktestRun, (BacktestRun.id == rid) & _stale(), (BacktestStatus.DONE,),
            status=BacktestStatus.PENDING, error=None, finished_at=None)
        await session.commit()
    return changed


async def _restore(rid: uuid.UUID) -> bool:
    """Put the backup back — row first, so a row deleted meanwhile does not get
    artifacts nothing points at — then drop the backup.

    Never over a `running` row. That is a worker writing this run's directory
    right now — a run past run_timeout, or a job picked up just as we gave up
    on it — and the row is its to finish: overwriting it would make the
    worker's final compare-and-set miss and delete the very artifacts put back
    here. False then, and nothing is touched, the backup included: the next
    session's _recover settles it once the run has ended.
    """
    backup = _backup_dir(rid)
    snap = json.loads((backup / "row.json").read_text())
    finished = snap["finished_at"]
    values = {c: snap[c] for c in _COLUMNS}
    values["metrics"] = null() if snap["metrics"] is None else snap["metrics"]
    values["finished_at"] = datetime.datetime.fromisoformat(finished) if finished else None
    async with async_session_maker() as session:
        exists = await transition(session, BacktestRun, BacktestRun.id == rid,
                                  (BacktestStatus.PENDING, *BACKTEST_FINISHED), **values)
        running = not exists and await session.scalar(
            select(BacktestRun.status).where(BacktestRun.id == rid)) == BacktestStatus.RUNNING
        await session.commit()
    if running:
        return False

    def _files() -> None:
        shutil.rmtree(artifacts_dir(rid), ignore_errors=True)
        if exists and (backup / "artifacts").is_dir():
            shutil.copytree(backup / "artifacts", artifacts_dir(rid))
        shutil.rmtree(backup)

    await asyncio.to_thread(_files)
    return True


async def _drop_backup(rid: uuid.UUID) -> None:
    await asyncio.to_thread(shutil.rmtree, _backup_dir(rid), True)


# ── the queue ─────────────────────────────────────────────────────────────────

async def _live_job(rid: uuid.UUID) -> dict | None:
    rows = await tasks.app.connector.execute_query_all_async(
        "SELECT id, status FROM procrastinate_jobs WHERE task_name = 'run_backtest'"
        " AND args->>'run_id' = %(rid)s AND status IN ('todo', 'doing')"
        " ORDER BY id DESC LIMIT 1", rid=str(rid))
    return rows[0] if rows else None


async def _defer(rid: uuid.UUID) -> None:
    await tasks.run_backtest.defer_async(run_id=str(rid))


async def _gave_up_queueing(rid: uuid.UUID) -> str | None:
    """Pending past the queue timeout: cancel the job if it is still queued.
    The reason to stop waiting, or None if it is being picked up right now."""
    job = await _live_job(rid)
    if job is None:
        return "lost (no queued job)"
    if job["status"] == "todo" and await tasks.app.job_manager.cancel_job_by_id_async(job["id"]):
        return "queue-timeout"
    return None


async def _wait(rid: uuid.UUID, queue_timeout: float, run_timeout: float) -> str | None:
    """Poll until the row leaves pending/running. None when it did; otherwise
    why we stopped waiting."""
    waiting_since, running_since, picked_up = time.monotonic(), None, False
    while True:
        status, _, _ = await _state(rid)
        if status not in BACKTEST_INFLIGHT:
            return None
        now = time.monotonic()
        if status == BacktestStatus.RUNNING:
            running_since = running_since or now
            if now - running_since > run_timeout:
                return "timeout"
        elif now - waiting_since > queue_timeout:
            # Once, a job a worker is picking up right now gets more time; a
            # second expiry is a worker that died holding it.
            reason = "stuck pending" if picked_up else await _gave_up_queueing(rid)
            if reason:
                return reason
            waiting_since, picked_up = now, True
        await asyncio.sleep(POLL_S)


async def _settle(rid: uuid.UUID, timeouts: tuple[float, float],
                  requeue: bool = False) -> tuple[str, str]:
    """Wait out the run, then keep its new result or restore the old one:
    ``("ok" | "failed" | "deleted", why)``. ``requeue``: defer it if it is
    pending with no job — a session killed between the reset and the defer."""
    if (requeue and (await _state(rid))[0] == BacktestStatus.PENDING
            and await _live_job(rid) is None):
        await _defer(rid)
    stopped = await _wait(rid, *timeouts)
    status, has_key, error = await _state(rid)
    if status is None:  # the tenant deleted it once it left pending/running
        await asyncio.to_thread(shutil.rmtree, artifacts_dir(rid), True)
        await _drop_backup(rid)
        return "deleted", "run deleted meanwhile"
    if stopped is None and status == BacktestStatus.DONE and has_key:
        await _drop_backup(rid)
        return "ok", ""
    why = stopped or f"{status}: {(error or '').strip()[-300:]}"
    if not await _restore(rid):
        return "failed", f"{why}; still running, backup kept for the next session"
    return "failed", why


# ── the task ──────────────────────────────────────────────────────────────────

async def _recover(timeouts) -> Counter:
    """Settle whatever a killed session left behind, before anything new."""
    out: Counter = Counter()
    base = _root() / "backup"
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if not (d / "row.json").exists():  # killed mid-backup: never reset
            await asyncio.to_thread(shutil.rmtree, d, True)
            continue
        rid = uuid.UUID(d.name)
        outcome, why = await _settle(rid, timeouts, requeue=True)
        out[f"recovered_{outcome}"] += 1
        log(f"recovered run={rid} from an interrupted session -> {outcome} {why}")
    return out


async def _rerun_one(rid: uuid.UUID, timeouts) -> tuple[str, str]:
    if not await _backup(rid):
        return "skipped", "no longer done"
    if not await _reset(rid):
        await _drop_backup(rid)
        return "skipped", "no longer qualifies"
    await _defer(rid)
    outcome, why = await _settle(rid, timeouts)
    if outcome == "failed":
        await asyncio.to_thread(_record_failure, rid)
    return outcome, why


async def _dry_run(tenants, failed: set[str]) -> dict:
    rows = await _rows(_qualifying(tenants))
    per_tenant = Counter(r.tenant_id for r in rows)
    for tid, n in per_tenant.most_common():
        log(f"tenant={tid} qualifying={n}")
    known = sum(1 for r in rows if str(r.id) in failed)
    base = _root() / "backup"
    leftovers = len(list(base.iterdir())) if base.is_dir() else 0
    log(f"dry run: {len(rows)} run(s) qualify across {len(per_tenant)} tenant(s); "
        f"{known} failed in an earlier session (skipped without --retry-failed); "
        f"{leftovers} backup(s) left by an interrupted session")
    return {"qualifying": len(rows), "tenants": len(per_tenant),
            "failed_before": known, "leftover_backups": leftovers}


async def rerun_backtests(*, dry_run: bool = False, limit: int | None = None,
                          tenants: list[uuid.UUID] | None = None,
                          budget_s: float | None = None, retry_failed: bool = False,
                          queue_timeout: float | None = None,
                          run_timeout: float | None = None) -> dict:
    failed = set() if retry_failed else _failed_before()
    if dry_run:
        return await _dry_run(tenants, failed)
    timeouts = (queue_timeout or 3 * settings.subprocess_timeout_s,
                run_timeout or settings.subprocess_timeout_s + RUN_MARGIN_S)
    started = time.monotonic()
    summary: Counter = Counter()
    async with tasks.app.open_async():
        summary.update(await _recover(timeouts))
        todo = [r for r in await _rows(_qualifying(tenants)) if str(r.id) not in failed]
        batch = todo[:limit] if limit else todo
        summary["selected"] = len(batch)
        for i, r in enumerate(batch, 1):
            if budget_s and time.monotonic() - started > budget_s:
                log("time budget spent — stopping; run again to continue")
                break
            t0 = time.monotonic()
            outcome, why = await _rerun_one(r.id, timeouts)
            summary[outcome] += 1
            log(f"[{i}/{len(batch)}] run={r.id} tenant={r.tenant_id} "
                f"strategy={r.strategy} -> {outcome} ({time.monotonic() - t0:.0f}s) {why}")
    summary["remaining"] = await _remaining(tenants)
    log(f"done in {time.monotonic() - started:.0f}s: " + ", ".join(
        f"{k}={v}" for k, v in sorted(summary.items())))
    return dict(summary)


async def _remaining(tenants) -> int:
    q = select(func.count()).select_from(_qualifying(tenants).subquery())
    async with async_session_maker() as session:
        return await session.scalar(q)
