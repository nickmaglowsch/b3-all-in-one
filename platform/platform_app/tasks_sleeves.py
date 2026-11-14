"""Keep every strategy sleeve's advised weights fresh, off the request path.

/portfolio/target and the rebalance plan read each strategy sleeve's weights
from sleeve_weights (see there). This job recomputes every family that some
book's sleeves currently need and whose stored answer is not fresh: after each
price ingest (the plane moved), shortly after the UTC day turns (advise_weights
runs to "today"), and whenever the API finds it serving a stale entry.

On the ingest queue: each family is a full market-plane build (~1.3 GB, 10-20 s),
which is the ingest worker's kind of work and the default worker's two
user-facing slots are not for. One family at a time, for the same reason.
Plain subprocesses: this runs in the worker, which has no zygotes.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from collections import defaultdict

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select

from platform_app import enginerun, portfolio_sleeves, sleeve_weights, specstore
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import PortfolioSleeve, RebalanceSettings, UserStrategy
from platform_app.tasks import app

log = logging.getLogger(__name__)
WARM = "warm_sleeve_weights"


class Need:
    """One family to keep warm: the strategy, its cadence, and for a user
    strategy the tenant whose specs the engine must see."""

    def __init__(self, ref: str, freq: str, tid: uuid.UUID | None, rows: list) -> None:
        self.ref, self.freq, self.tid, self.rows = ref, freq, tid, rows


def needs(sleeves: list[tuple], cadences: dict, users: dict) -> dict[str, Need]:
    """{family: Need} for every strategy sleeve of every book, at the cadences
    the target ('ME') and the book's rebalance plan (its cadence) ask for.
    ``sleeves`` is (tenant_id, strategy_ref) rows; ``users`` the tenant's
    UserStrategy rows by tenant. Pure, so the family rule is testable alone."""
    out: dict[str, Need] = {}
    for tid, ref in sleeves:
        if not ref or ref == portfolio_sleeves.CASH:
            continue
        mine = {r.name: r for r in users.get(tid, [])}
        for freq in sorted({"ME", cadences.get(tid) or "ME"}):
            row = mine.get(ref)
            if row is not None:
                stamp = row.updated_at.isoformat() if row.updated_at else ""
                fam = sleeve_weights.family(ref, freq, tid, stamp)
                out[fam] = Need(ref, freq, tid, users[tid])
            else:
                out.setdefault(sleeve_weights.family(ref, freq), Need(ref, freq, None, []))
    return out


async def _load_needs(session) -> dict[str, Need]:
    sleeves = (await session.execute(
        select(PortfolioSleeve.tenant_id, PortfolioSleeve.strategy_ref)
        .where(PortfolioSleeve.strategy_ref.is_not(None)))).all()
    tids = {t for t, _ in sleeves}
    cadences = dict((await session.execute(
        select(RebalanceSettings.tenant_id, RebalanceSettings.cadence)
        .where(RebalanceSettings.tenant_id.in_(tids)))).all())
    users: dict = defaultdict(list)
    for row in (await session.scalars(
            select(UserStrategy).where(UserStrategy.tenant_id.in_(tids)))).all():
        users[row.tenant_id].append(row)
    return needs(sleeves, cadences, users)


def _advise(need: Need) -> dict:
    extra_env = specstore.specs_env(need.rows, need.tid) if need.tid else {}
    return enginerun.advise_weights(strategy=need.ref,
                                    params=sleeve_weights.advise_params(need.freq),
                                    extra_env=extra_env or None)


async def warm() -> dict:
    """Recompute every needed family whose stored answer is not fresh."""
    today, plane = datetime.date.today(), await sleeve_weights.plane_date()
    async with async_session_maker() as session:
        todo = await _load_needs(session)
        stored = await sleeve_weights.lookup(session, list(todo))
        await session.commit()
    stale = {f: n for f, n in todo.items()
             if f not in stored or not stored[f].fresh(today, plane)}
    done, failed = 0, []
    for fam, need in stale.items():
        try:
            payload = await asyncio.to_thread(_advise, need)
        except Exception as e:  # noqa: BLE001 — one strategy must not stop the rest
            # A needs-fundamentals strategy lands here every night until the CVM
            # ingest has run; the request path reports it, this only logs it.
            log.warning("warm %s failed: %s", need.ref, str(e)[-500:])
            failed.append(need.ref)
            continue
        async with async_session_maker() as session:
            await sleeve_weights.store(session, fam, need.tid, today, plane, payload)
        done += 1
    return {"needed": len(todo), "refreshed": done, "failed": failed}


# Minutes after midnight UTC: advise_weights runs to "today", so every entry is
# a day old the moment the date turns. The post-ingest refresh is deferred by
# tasks_ingest.ingest_prices itself, and a stale read by the API defers one too.
@app.periodic(cron=settings.warm_sleeves_cron)
@app.task(name=WARM, queue="ingest", queueing_lock=WARM)
async def warm_sleeve_weights(timestamp: int = 0) -> dict:
    return await warm()


async def defer_warm() -> None:
    """Queue a refresh unless one is already waiting. Opens the app's pool,
    so a caller inside a worker (whose app is open) uses defer_warm_open."""
    async with app.open_async():
        await defer_warm_open()


async def defer_warm_open() -> None:
    try:
        await warm_sleeve_weights.defer_async()
    except AlreadyEnqueued:
        pass  # the waiting one will see everything this one would
