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
each ingest that lands, after the UTC day rolls over, and whenever a request finds a
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
from sqlalchemy import Date, DateTime, Text, delete, func, select, true
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
    # Every price plane's last session when computed (plane_date()).
    plane: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Entry(NamedTuple):
    """One family's latest answer."""
    day: datetime.date
    plane: str | None
    payload: dict

    def fresh(self, today: datetime.date, plane: str | None) -> bool:
        return self.day == today and self.plane == plane


# Read-through over the table, for fresh answers only: a stale one is re-read,
# because the refresh that replaces it is written by another process (the
# ingest worker) and only the table sees it. Bounded like the API's other memos.
_MEM: dict[str, Entry] = {}
_MEM_MAX = 1024


def clear_memory() -> None:
    _MEM.clear()


def family(ref: str, freq: str, tid: uuid.UUID | None = None,
           user_stamp: str | None = None) -> str:
    """The cache identity of a sleeve. A user strategy's carries its tenant (two
    tenants' same-named specs are different strategies) and the tenant's latest
    user-strategy edit (an edit is a new strategy — and a user portfolio is built
    from the tenant's other user strategies, so editing one of those is too)."""
    if tid is None:
        return json.dumps([ref, freq])
    return json.dumps([ref, freq, str(tid), user_stamp or ""])


def user_stamp(rows) -> str:
    """The tenant's latest user-strategy edit, over ``rows`` (UserStrategy)."""
    stamps = [r.updated_at for r in rows if r.updated_at]
    return max(stamps).isoformat() if stamps else ""


def advise_params(freq: str) -> dict | None:
    """advise_weights' --params for a cadence; 'ME' is the strategy's default."""
    return None if freq == "ME" else {"rebalance_freq": freq}


# Each plane's last session. A strategy may trade B3, the US or crypto, and each
# is refreshed by its own ingest at its own hour, so the stamp names all three.
# Every arm is one probe of a (market, date) / (date) index.
_PLANE_SQL = """
SELECT (SELECT max(date) FROM prices) AS b3,
       (SELECT max(date) FROM market_prices WHERE market = 'US') AS us,
       (SELECT max(date) FROM market_prices WHERE market = 'CRYPTO') AS crypto
"""


async def plane_date() -> str | None:
    """What a fresh entry was computed against: every price plane's last
    session. None when the plane cannot be read (an entry then ages by the day)."""
    try:
        pool = await get_engine_pool()
        row = await pool.fetchrow(_PLANE_SQL)
    except Exception:  # noqa: BLE001 — an unreadable plane is not this module's error
        return None
    return " ".join(f"{k}={row[k].isoformat() if row[k] else '-'}" for k in ("b3", "us", "crypto"))


async def lookup(session, families: list[str], today: datetime.date | None = None,
                 plane: str | None = None) -> dict[str, Entry]:
    """The stored entry of each family that has one. With ``today``, only a
    fresh memo is trusted; anything older is read again from the table."""
    out = {f: e for f in families if (e := _MEM.get(f)) is not None
           and (today is None or e.fresh(today, plane))}
    missing = [f for f in families if f not in out]
    if missing:
        rows = (await session.execute(
            select(SleeveWeights.family, SleeveWeights.day, SleeveWeights.plane,
                   SleeveWeights.payload)
            .where(SleeveWeights.family.in_(missing)))).all()
        for fam, day, plane_, payload in rows:
            out[fam] = _remember(fam, Entry(day, plane_, payload))
    return out


def _remember(fam: str, entry: Entry) -> Entry:
    if len(_MEM) > _MEM_MAX:
        _MEM.clear()
    _MEM[fam] = entry
    return entry


async def prune(session, keep: set[str], older_than: datetime.timedelta) -> int:
    """Drop the families nothing needs any more (an edited strategy's previous
    stamp, a strategy no sleeve holds) once they are ``older_than``."""
    res = await session.execute(delete(SleeveWeights).where(
        SleeveWeights.family.not_in(keep) if keep else true(),
        SleeveWeights.computed_at < func.now() - older_than))
    await session.commit()
    return res.rowcount or 0


async def store(session, fam: str, tid: uuid.UUID | None, day: datetime.date,
                plane: str | None, payload: dict) -> None:
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


def refresh_soon(today: datetime.date, plane: str | None) -> None:
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
