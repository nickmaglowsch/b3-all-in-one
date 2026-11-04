"""Where a user strategy is named: the live sleeves and the tenant's other
portfolios. Split out of routes.py — the rename cascade, the delete guard and
the update guard (a sleeve may not move off B3) all walk the same two places.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.models import PortfolioSleeve, UserStrategy


async def _other_portfolios(
    session: AsyncSession, tid: uuid.UUID, exclude_id: uuid.UUID
) -> list[UserStrategy]:
    """The tenant's user portfolios (fixed_weight) other than ``exclude_id`` —
    the only specs that can name another strategy in a sleeve."""
    return list(await session.scalars(
        select(UserStrategy).where(
            UserStrategy.tenant_id == tid, UserStrategy.kind == "fixed_weight",
            UserStrategy.id != exclude_id,
        )
    ))


async def _cascade_rename(
    session: AsyncSession, tid: uuid.UUID, old: str, new: str, exclude_id: uuid.UUID
) -> None:
    """Rename old→new across the tenant's live sleeves and other user portfolios
    that reference it (keeps the registry key consistent)."""
    await session.execute(
        update(PortfolioSleeve)
        .where(PortfolioSleeve.tenant_id == tid, PortfolioSleeve.strategy_ref == old)
        .values(strategy_ref=new)
    )
    for o in await _other_portfolios(session, tid, exclude_id):
        sleeves = o.spec.get("sleeves", [])
        if any(sl.get("strategy") == old for sl in sleeves):
            o.spec = {**o.spec, "sleeves": [
                {**sl, "strategy": new} if sl.get("strategy") == old else sl
                for sl in sleeves
            ]}


async def _refuse_if_used(session, tid, row, why: str = "") -> None:
    """409 when `row` is a live sleeve or another portfolio's sleeve."""
    if await session.scalar(select(func.count()).select_from(PortfolioSleeve).where(
            PortfolioSleeve.tenant_id == tid, PortfolioSleeve.strategy_ref == row.name)):
        raise HTTPException(409, f"{row.name!r} is a live sleeve{why} — remove it from /portfolio/sleeves first")
    for o in await _other_portfolios(session, tid, row.id):
        if any(sl.get("strategy") == row.name for sl in o.spec.get("sleeves", [])):
            raise HTTPException(409, f"{row.name!r} is referenced by portfolio {o.name!r}{why}")
