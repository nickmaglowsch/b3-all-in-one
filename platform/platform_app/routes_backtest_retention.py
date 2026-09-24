"""Getting rid of backtest runs: one at a time, or a whole cohort of old ones.

The bench only ever grew. Every submission — including the ones that died in the
engine seconds later — stayed on the list forever, each carrying a metrics JSONB
blob and a directory of artifacts (curves.csv, metrics.json) nothing would ever
read again. GET /backtests capped the *page* at 500 rows, which stopped the
listing from growing without bound but did nothing about the rows or the disk
behind it, and left the user staring at a wall of `Erro` from an afternoon of
failed experiments with no way to clear it.

Two verbs, because a bench accumulates junk in two shapes:

  DELETE /backtests/{id}   — this run, right here, was a mistake.
  DELETE /backtests?...    — everything older than X (optionally only the errors),
                             keeping the N most recent whatever the criteria say.

Registered in main.py next to `backtests_router` and gated on `bench` with it:
same resource, same module, and a router of its own only because routes.py is at
its size ceiling.

Deliberately manual, not a retention *policy*. A nightly sweep that silently ate
a run someone was about to open is a support ticket nobody can reconstruct; a
button the user presses is a decision they made. If a policy is wanted later,
this module already holds the query it would run.
"""
from __future__ import annotations

import datetime
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.config import settings
from platform_app.db import get_async_session
from platform_app.models import BacktestRun
from platform_app.tenancy import get_current_tenant, get_owned

backtest_retention_router = APIRouter(prefix="/backtests", tags=["backtests"])

# A finished run is the only kind that can go. `pending`/`running` belong to the
# worker: tasks.run_backtest holds the row in its own session across a subprocess
# that runs for minutes, and deleting it underneath would either resurrect the
# row on the worker's next commit or blow up its flush — while the engine keeps
# writing artifacts into a directory nothing points at any more. The quota only
# counts runs in flight (routes.inflight_backtests), so refusing here also means
# deletion can never be a way to slip past admission control.
FINISHED = ("done", "error")

# Ceiling on `keep`, mirroring the listing's own `le=500`: the parameter exists to
# protect the recent runs, and a number past the page that shows them is asking
# for a policy, not a sweep.
MAX_KEEP = 500

# Chunk for the DELETE ... WHERE id IN (...). The sweep deletes by the exact ids
# it priced, not by re-running the predicate, so a run that finishes between the
# two statements is not deleted with its artifacts left behind — but a tenant
# with a thousand dead runs would otherwise send a thousand-item IN list in one
# statement.
_CHUNK = 500


def artifacts_dir(run_id: uuid.UUID | str) -> Path:
    """Where tasks.run_backtest told the engine to write. Built from the id, not
    read from `artifacts_path`: that column is only written on the success path,
    while the engine creates the directory and writes curves.csv *before*
    anything can fail — so trusting the column leaves a failed run's NAV curve on
    disk with no row pointing at it. Same reasoning, same expression, as
    routes_portfolios.purge_tenant_rows."""
    return Path(settings.artifacts_dir).resolve() / str(run_id)


def _unlink(run_ids: list[uuid.UUID]) -> None:
    """Artifacts last, after the rows are committed — a failed rmtree must not
    keep the rows alive (routes_portfolios.purge_tenant_files takes the same
    order for the same reason)."""
    for rid in run_ids:
        shutil.rmtree(artifacts_dir(rid), ignore_errors=True)


def _parse_statuses(status: str | None) -> list[str]:
    """The statuses to sweep, defaulting to every finished one.

    An unknown name is a 422 rather than an empty sweep: "nothing matched" and
    "you typed `errors` and nothing will ever match" look identical in a
    `{"deleted": 0}`, and the second one is a caller quietly never cleaning up.
    Asking for `pending`/`running` is refused by name, because the caller means
    something real that this route will not do.
    """
    if not status:
        return list(FINISHED)
    asked = [s.strip() for s in status.split(",") if s.strip()]
    inflight = [s for s in asked if s in ("pending", "running")]
    if inflight:
        raise HTTPException(
            422, f"rodadas em andamento não podem ser excluídas: {', '.join(inflight)}")
    unknown = [s for s in asked if s not in FINISHED]
    if unknown:
        raise HTTPException(422, f"status desconhecido: {', '.join(unknown)}")
    return [s for s in FINISHED if s in asked]


@backtest_retention_router.delete("/{run_id}", status_code=204)
async def delete_backtest(
    run_id: str,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Drop one run and its artifacts. 404 for another tenant's run — the same
    answer routes._get_run gives, so deleting cannot be used to probe for ids
    that exist elsewhere."""
    _user, tid = tenant
    run = await get_owned(session, BacktestRun, run_id, tid, "backtest not found")
    if run.status not in FINISHED:
        raise HTTPException(
            409, f"rodada em andamento (status={run.status}) — aguarde terminar")

    rid = run.id
    await session.delete(run)
    await session.commit()
    _unlink([rid])
    return Response(status_code=204)


@backtest_retention_router.delete("")
async def purge_backtests(
    # A cutoff instant, not a "days" count: the caller already knows which day it
    # means, and a date on the wire is a number the response can be checked
    # against. Runs created strictly before local midnight UTC of this date go.
    before: datetime.date | None = Query(None),
    status: str | None = Query(None),
    keep: int = Query(0, ge=0, le=MAX_KEEP),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Sweep old runs. Returns `{"deleted": n}` — the count is the point, since
    the caller cannot know from its own page how many rows past it matched.

    At least one criterion is required. A bare `DELETE /backtests` reads as
    "clear the bench" to whoever typed it and as "delete every backtest I have
    ever run" to the database, and the gap between those two is not something to
    discover from a 200. `keep` alone is a criterion: "trim to the last N".

    `keep` protects the N most recent runs of the tenant regardless of status or
    age — including the ones still in flight, which are never candidates anyway.
    It is applied to the whole book rather than to the matched set so that "keep
    10" means what it says on a bench where the last ten runs all failed.
    """
    _user, tid = tenant
    if before is None and status is None and keep == 0:
        raise HTTPException(
            422, "informe ao menos um critério: before, status ou keep")
    statuses = _parse_statuses(status)

    protected: set[uuid.UUID] = set()
    if keep:
        protected = set((await session.scalars(
            select(BacktestRun.id).where(BacktestRun.tenant_id == tid)
            .order_by(BacktestRun.created_at.desc())
            .limit(keep)
        )).all())

    q = select(BacktestRun.id).where(
        BacktestRun.tenant_id == tid, BacktestRun.status.in_(statuses)
    )
    if before is not None:
        # Explicit UTC midnight. created_at is timestamptz and a bare date would
        # be cast in whatever timezone the session happens to carry, which is how
        # a sweep deletes a run the user can still see (or spares one they can't)
        # depending on the server's locale.
        q = q.where(BacktestRun.created_at < datetime.datetime.combine(
            before, datetime.time.min, tzinfo=datetime.UTC))

    victims = [rid for rid in (await session.scalars(q)).all() if rid not in protected]
    for i in range(0, len(victims), _CHUNK):
        await session.execute(
            delete(BacktestRun).where(BacktestRun.id.in_(victims[i:i + _CHUNK])))
    await session.commit()
    _unlink(victims)
    return {"deleted": len(victims)}
