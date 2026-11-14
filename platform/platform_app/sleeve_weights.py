"""Each strategy sleeve's advised weights, kept where every process can read them.

A strategy sleeve's target comes from advise_weights, which builds the whole
market plane and runs the strategy over twenty years of it: 10–20 s per sleeve.
That used to be cached in API memory under today's date, so every deploy and
every UTC midnight handed the full cost to whoever opened /estrategia next.

So the latest answer per *family* — a strategy at a cadence, plus the tenant and
edit stamp for a user strategy — lives in this table, one row per family, and:

  · fresh (computed today, against the plane's current last session): served;
  · stale (an older day or plane): served, and a background refresh deferred —
    yesterday's target for the minutes the refresh takes beats a 20 s page;
  · absent (a strategy nobody has asked for, a user strategy just edited — its
    edit stamp is part of the family): computed inline once, then stored.

The refresh is tasks_sleeves.warm_sleeve_weights, on the ingest worker: after
each price ingest, after the UTC day rolls over, and whenever a request finds a
stale entry. Nothing here imports the engine; computing is the caller's.

Apart from models.py only because that module is at its size ceiling; it is
imported there so the table registers with the others. A user strategy's rows
carry its tenant_id, so the LGPD export/erasure sweeps take them along.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import uuid
from typing import NamedTuple

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import Date, DateTime, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from platform_app.db import Base, get_engine_pool

log = logging.getLogger(__name__)


class SleeveWeights(Base):
    __tablename__ = "sleeve_weights"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # json [ref, freq] for a built-in, [ref, freq, tenant, updated_at] for a user's.
    family: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, index=True, nullable=True)
    day: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    plane: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Entry(NamedTuple):
    """One family's latest answer."""
    day: datetime.date
    plane: datetime.date | None
    payload: dict

    def fresh(self, today: datetime.date, plane: datetime.date | None) -> bool:
        return self.day == today and self.plane == plane


# Read-through over the table: a hit here skips even the indexed SELECT. Bounded
# like the API's other memos; dropping it costs one query to refill.
_MEM: dict[str, Entry] = {}
_MEM_MAX = 1024


def clear_memory() -> None:
    _MEM.clear()


def family(ref: str, freq: str, tid: uuid.UUID | None = None,
           updated_at: str | None = None) -> str:
    """The cache identity of a sleeve. A user strategy's carries its tenant (two
    tenants' same-named specs are different strategies) and its edit stamp (an
    edit is a new strategy, never served the old one's weights)."""
    if tid is None:
        return json.dumps([ref, freq])
    return json.dumps([ref, freq, str(tid), updated_at or ""])


def advise_params(freq: str) -> dict | None:
    """advise_weights' --params for a cadence; 'ME' is the strategy's default."""
    return None if freq == "ME" else {"rebalance_freq": freq}


async def plane_date() -> datetime.date | None:
    """The price plane's last session — what a fresh entry was computed against.
    None when the plane cannot be read (the entry then only ages by the day)."""
    try:
        pool = await get_engine_pool()
        return await pool.fetchval("SELECT max(date) FROM prices")
    except Exception:  # noqa: BLE001 — an unreadable plane is not this module's error
        return None


async def lookup(session, families: list[str]) -> dict[str, Entry]:
    """The stored entry of each family that has one."""
    out = {f: _MEM[f] for f in families if f in _MEM}
    missing = [f for f in families if f not in out]
    if missing:
        rows = (await session.execute(
            select(SleeveWeights.family, SleeveWeights.day, SleeveWeights.plane,
                   SleeveWeights.payload)
            .where(SleeveWeights.family.in_(missing)))).all()
        for fam, day, plane, payload in rows:
            out[fam] = _remember(fam, Entry(day, plane, payload))
    return out


def _remember(fam: str, entry: Entry) -> Entry:
    if len(_MEM) > _MEM_MAX:
        _MEM.clear()
    _MEM[fam] = entry
    return entry


async def store(session, fam: str, tid: uuid.UUID | None, day: datetime.date,
                plane: datetime.date | None, payload: dict) -> None:
    """Upsert the family's latest answer and commit."""
    values = {"day": day, "plane": plane, "payload": payload, "tenant_id": tid,
              "computed_at": func.now()}
    await session.execute(
        pg_insert(SleeveWeights).values(family=fam, **values)
        .on_conflict_do_update(index_elements=[SleeveWeights.family], set_=values))
    await session.commit()
    _remember(fam, Entry(day, plane, payload))


# One deferred refresh per (day, plane) per process: the task's queueing_lock
# already dedupes in the queue, this saves opening a pool per stale request.
_requested: set[tuple] = set()
_pending: set[asyncio.Task] = set()


def refresh_soon(today: datetime.date, plane: datetime.date | None) -> None:
    """Ask the ingest worker to recompute stale families, off the request."""
    key = (today, plane)
    if key in _requested:
        return
    _requested.clear()
    _requested.add(key)

    async def _defer() -> None:
        # Imported here: the task module needs procrastinate's app, which this
        # module (imported by models.py) must not pull in.
        from platform_app import tasks_sleeves
        with contextlib.suppress(Exception):
            await tasks_sleeves.defer_warm()

    task = asyncio.create_task(_defer())
    _pending.add(task)
    task.add_done_callback(_pending.discard)
