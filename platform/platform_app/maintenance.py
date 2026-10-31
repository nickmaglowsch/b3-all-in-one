"""Recompute derived data for existing users after a fix ships.

    python -m platform_app.maintenance reprocess-imports [--dry-run] [--limit N] [--tenant ID]
    python -m platform_app.maintenance rerun-backtests  [--dry-run] [--limit N] [--tenant ID]
                                                        [--budget-minutes M] [--retry-failed]

Run inside the WORKER container — it has the taxcore and engine venvs, the
uploads and artifacts volumes and the procrastinate queue. In production that is
`.github/workflows/maintenance.yml` (Actions → Maintenance → Run workflow), which
does `docker compose ... exec -T worker python -m platform_app.maintenance ...`.

Derived rows are only ever rebuilt by the event that created them: a book by an
upload, a backtest by a submission. A parser or engine fix therefore leaves every
existing tenant on the numbers the bug produced until they happen to act again.
These two tasks are that "again", for everyone, on demand:

  reprocess-imports — defer tasks.process_imports once per tenant that has an
      Import. The task itself does the work and the bookkeeping (it full-replaces
      trades, cash events, positions and renda fixa marks, drops the TaxSummary
      cache and moves every Import through processing → done/error), so imports
      left `processing` by an older failure are picked up as well.
  rerun-backtests — see maintenance_backtests.

--tenant (repeatable) narrows either task to the given books; --dry-run prints
what would happen and writes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import Counter
from pathlib import Path

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select

from platform_app import tasks
from platform_app.db import async_session_maker, engine
from platform_app.maintenance_backtests import log, rerun_backtests
from platform_app.models import Import

# Below the 0 every user-triggered job carries, and procrastinate fetches by
# priority first: a tenant who uploads or submits a backtest while this backlog
# drains goes ahead of it instead of waiting behind every other book.
MAINTENANCE_PRIORITY = -1


async def _import_inventory(tenants) -> tuple[dict, dict]:
    """``({tenant: Counter(status)}, {tenant: [missing stored_path]})``, tenants
    in order of their oldest import."""
    q = select(Import.tenant_id, Import.status, Import.stored_path).order_by(
        Import.created_at, Import.id)
    if tenants:
        q = q.where(Import.tenant_id.in_(tenants))
    async with async_session_maker() as session:
        rows = (await session.execute(q)).all()
    statuses: dict[uuid.UUID, Counter] = {}
    for tid, status, _ in rows:
        statuses.setdefault(tid, Counter())[status] += 1
    gone = await asyncio.to_thread(
        lambda: [(tid, p) for tid, _, p in rows if not Path(p).is_file()])
    missing: dict[uuid.UUID, list[str]] = {}
    for tid, p in gone:
        missing.setdefault(tid, []).append(p)
    return statuses, missing


async def _queued_import_tenants() -> set[str]:
    """Tenants with a process_imports job not yet started. That job reads the
    imports when it runs, so it already covers everything a new one would."""
    rows = await tasks.app.connector.execute_query_all_async(
        "SELECT DISTINCT args->>'tenant_id' AS tid FROM procrastinate_jobs"
        " WHERE task_name = 'process_imports' AND status = 'todo'")
    return {r["tid"] for r in rows}


async def _defer_import(tid: uuid.UUID, missing: list[str] | None,
                        queued: set[str], dry_run: bool) -> str:
    if missing:
        # process_imports would die copying the first absent extract and leave
        # every one of the tenant's imports `processing` — worse than the old
        # numbers they have now. Listed for a human instead.
        log(f"tenant={tid} has {len(missing)} extract(s) missing on disk, e.g. {missing[0]}")
        return "skipped_missing_files"
    if str(tid) in queued:
        return "skipped_already_queued"
    if dry_run:
        return "would_defer"
    key = tasks.imports_lock(tid)
    try:
        # Same locks as an upload's defer: without them this job could run
        # beside the tenant's own and take both worker slots.
        await tasks.process_imports.configure(
            priority=MAINTENANCE_PRIORITY, queueing_lock=key, lock=key,
        ).defer_async(tenant_id=str(tid))
    except AlreadyEnqueued:
        # An upload's job is already waiting and will read the whole book.
        return "skipped_already_queued"
    return "deferred"


def _fmt(counts: Counter) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"


async def reprocess_imports(*, dry_run: bool = False, limit: int | None = None,
                            tenants: list[uuid.UUID] | None = None) -> dict:
    statuses, missing = await _import_inventory(tenants)
    log(f"imports by status: {_fmt(sum(statuses.values(), Counter()))}")
    selected = list(statuses)[:limit] if limit else list(statuses)
    summary = Counter(tenants_found=len(statuses), tenants_selected=len(selected))
    async with tasks.app.open_async():
        queued = await _queued_import_tenants()
        for tid in selected:
            outcome = await _defer_import(tid, missing.get(tid), queued, dry_run)
            summary[outcome] += 1
            log(f"tenant={tid} imports[{_fmt(statuses[tid])}] -> {outcome}")
    log(("dry run: " if dry_run else "") + _fmt(summary))
    return dict(summary)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m platform_app.maintenance",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("task", choices=["reprocess-imports", "rerun-backtests"])
    p.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    p.add_argument("--limit", type=_positive_int, help="at most N tenants / runs")
    p.add_argument("--tenant", type=uuid.UUID, action="append", help="only this book")
    p.add_argument("--budget-minutes", type=_positive_int,
                   help="rerun-backtests: start no new run after M minutes")
    p.add_argument("--retry-failed", action="store_true",
                   help="rerun-backtests: include runs an earlier session failed on")
    return p


async def _run(args: argparse.Namespace) -> dict:
    try:
        if args.task == "reprocess-imports":
            return await reprocess_imports(
                dry_run=args.dry_run, limit=args.limit, tenants=args.tenant)
        return await rerun_backtests(
            dry_run=args.dry_run, limit=args.limit, tenants=args.tenant,
            budget_s=args.budget_minutes and args.budget_minutes * 60,
            retry_failed=args.retry_failed)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = asyncio.run(_run(args))
    # Red in Actions when a run had to be restored, so someone reads the list.
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
