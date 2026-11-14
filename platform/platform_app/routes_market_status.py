"""GET /market/status — what the engine market plane holds, and how fresh it is.

Split out of routes.py on size (scripts/quality_gates.py); the SQL itself lives
in market_planes.py. Free for everyone: the dashboard polls it from every tab.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import market_planes
from platform_app.db import get_async_session, get_engine_pool
from platform_app.models import IngestRun

market_router = APIRouter(prefix="/market", tags=["market"])


# ── Market status ─────────────────────────────────────────────────────────────
# count(*) + count(DISTINCT ticker) over `prices` is an aggregate across the whole
# table (89 ms at 257k rows, and it grows linearly with the plane). The dashboard
# polls this endpoint on a timer from every open tab, so the cost used to scale
# with the number of people looking at it. One cached answer per TTL decouples the
# two: the plane only changes when the ingestion pipeline runs, which is a manual,
# infrequent job.
#
# And no request pays for the aggregate either, past the first. It is seconds on
# a plane with tens of millions of US bars, so when the entry is older than the
# TTL the stale one is served and a single background task recomputes it
# (stale-while-revalidate); the API's lifespan fills it at boot. The longer TTL
# is affordable for the same reason it is harmless: the plane moves once a day.
_MARKET_STATUS_TTL = datetime.timedelta(seconds=300)
_market_status_cache: tuple[datetime.datetime, dict] | None = None
_market_status_refresh: asyncio.Task | None = None


async def refresh_market_status() -> dict:
    """Recompute and store the plane summary. Raises when the plane is
    unreachable — the stale entry, if any, is left for the next try."""
    global _market_status_cache
    payload = await _market_status_uncached()
    _market_status_cache = (datetime.datetime.now(datetime.UTC), payload)
    return payload


def _refresh_market_status_soon() -> None:
    """At most one background recompute at a time."""
    global _market_status_refresh
    if _market_status_refresh is not None and not _market_status_refresh.done():
        return

    async def _run() -> None:
        with contextlib.suppress(Exception):
            await refresh_market_status()

    _market_status_refresh = asyncio.create_task(_run())


async def _last_ingest(session: AsyncSession) -> dict[str, dict]:
    """Latest ingestion attempt per kind. `error` is deliberately not exposed —
    it carries subprocess output with server paths (privacy._INTERNAL_COLUMNS
    strips the same field for the same reason); `status` is the part a caller
    needs. Never fails the status call: knowing the plane is stale matters more
    than knowing why."""
    out: dict[str, dict] = {}
    try:
        # DISTINCT ON, not "read everything and keep the first of each kind in
        # Python". ingest_runs grows by a row per ingestion attempt and never
        # shrinks, and this endpoint is polled on a timer from every open tab, so
        # the old form made the cost of answering "is the plane fresh?" scale with
        # how often the plane had been refreshed. ix_ingest_runs_kind_started
        # already indexes exactly this; only the query was wrong.
        #
        # The column list is explicit so `error` never leaves the database — it
        # carries subprocess output with server paths.
        rows = (await session.execute(
            select(IngestRun.kind, IngestRun.status,
                   IngestRun.started_at, IngestRun.finished_at)
            .distinct(IngestRun.kind)
            .order_by(IngestRun.kind, IngestRun.started_at.desc())
        )).all()
    except Exception:
        return out
    for kind, status, started_at, finished_at in rows:
        out[kind] = {
            "status": status,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
        }
    return out


# What an absent market plane looks like. A fresh deploy has no `prices` table at
# all until the first ingest runs, and aggregating a missing table raises — so the
# status endpoint used to 500 on exactly the environment that most needs to report
# "there is no data yet". Same posture as /positions and /price-history: the plane
# is optional, its absence is a fact to report, not an error.
_EMPTY_PLANE = market_planes._EMPTY_PLANE


async def _market_status_uncached() -> dict:
    return await market_planes.status(await get_engine_pool())


# Prices are expected every trading day. Four calendar days absorbs a long weekend
# plus a feriado without crying wolf; past that the plane is genuinely behind.
_PRICES_STALE_AFTER_DAYS = 4


@market_router.get("/status")
async def market_status(
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Health of the engine market plane (plain SQL over ENGINE_DATABASE_URL).
    0-row tables are reported honestly (fresh: null). Cached for _MARKET_STATUS_TTL.

    `stale` is the field worth acting on: this plane sat seven months behind while
    every position was marked from it, and nothing said so.
    """
    now = datetime.datetime.now(datetime.UTC)
    if _market_status_cache:
        payload = _market_status_cache[1]
        if now - _market_status_cache[0] >= _MARKET_STATUS_TTL:
            _refresh_market_status_soon()
    else:
        try:
            payload = await refresh_market_status()
        except Exception:
            # Not cached: an unreachable plane should recover on the next call
            # rather than be pinned as empty for the whole TTL.
            return {**_EMPTY_PLANE, "prices_age_days": None, "stale": True,
                    "last_ingest": await _last_ingest(session)}

    max_date = payload["prices"]["max_date"]
    age_days = (
        (now.date() - date.fromisoformat(max_date)).days if max_date else None
    )
    # Not cached with the payload: ingest rows change on their own schedule, and
    # this is one indexed query against the platform DB.
    return {
        **payload,
        "prices_age_days": age_days,
        "stale": age_days is None or age_days > _PRICES_STALE_AFTER_DAYS,
        "last_ingest": await _last_ingest(session),
    }
