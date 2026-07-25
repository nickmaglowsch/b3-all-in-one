"""The books an account manages (spec/07, enterprise tier).

Everyone has exactly one own book, whose tenant_id is the user id and which has
no row in `portfolios` — that is why nothing needed backfilling when this landed.
The enterprise tier adds client books: rows here, whose id is the tenant_id the
X-Portfolio-Id header selects (see tenancy.get_current_tenant).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.billing import needs_enterprise
from platform_app.config import settings
from platform_app.db import Base, get_async_session
from platform_app.models import BacktestRun, Portfolio, User
from platform_app.users import current_active_user

portfolios_router = APIRouter(prefix="/portfolios", tags=["portfolios"])

SELF_LABEL = "Minha carteira"


class PortfolioIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)


def _out(p: Portfolio) -> dict:
    return {"id": str(p.id), "label": p.label, "is_self": False}


def _clean(label: str) -> str:
    if not (cleaned := label.strip()):
        raise HTTPException(422, "nome da carteira vazio")
    return cleaned


async def _owned(session: AsyncSession, portfolio_id: uuid.UUID, owner_id: uuid.UUID) -> Portfolio:
    """404 rather than 403 — the own book included, which has no row and so can
    be neither renamed nor deleted."""
    row = await session.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.owner_id == owner_id)
    )
    if row is None:
        raise HTTPException(404, "carteira não encontrada")
    return row


@portfolios_router.get("")
async def list_portfolios(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """Own book first, then the managed ones. Open to every tier: a free account
    simply gets a one-item list, which is what the switcher renders from.

    Takes the user, not the tenant: this is the call a client recovers a stale or
    unentitled selection with, so it must be the one route that cannot 402 or 404
    because of one.
    """
    rows = (
        await session.scalars(
            select(Portfolio).where(Portfolio.owner_id == user.id).order_by(Portfolio.label)
        )
    ).all()
    return [{"id": str(user.id), "label": SELF_LABEL, "is_self": True}, *map(_out, rows)]


@portfolios_router.post("", status_code=201)
async def create_portfolio(
    body: PortfolioIn,
    tenant=Depends(needs_enterprise),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    user, _ = tenant
    row = Portfolio(owner_id=user.id, label=_clean(body.label))
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"já existe uma carteira chamada {body.label!r}") from None
    return _out(row)


@portfolios_router.patch("/{portfolio_id}")
async def rename_portfolio(
    portfolio_id: uuid.UUID,
    body: PortfolioIn,
    tenant=Depends(needs_enterprise),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    user, _ = tenant
    row = await _owned(session, portfolio_id, user.id)
    row.label = _clean(body.label)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"já existe uma carteira chamada {body.label!r}") from None
    return _out(row)


@portfolios_router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(
    portfolio_id: uuid.UUID,
    tenant=Depends(needs_enterprise),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Offboard a client: the book and everything keyed to it, in one transaction.

    Sweeping by column instead of by model list means a tenant table added later
    is covered without anyone remembering this route exists.
    """
    user, _ = tenant
    row = await _owned(session, portfolio_id, user.id)

    artifacts = (
        await session.scalars(
            select(BacktestRun.artifacts_path).where(BacktestRun.tenant_id == portfolio_id)
        )
    ).all()
    # reversed: sorted_tables is parent-first, and a delete has to run child-first.
    # No tenant table references another today, so this only matters for the next
    # one — which is exactly the case the sweep claims to cover for free.
    for table in reversed(Base.metadata.sorted_tables):
        if "tenant_id" in table.c:
            await session.execute(delete(table).where(table.c.tenant_id == portfolio_id))
    await session.delete(row)
    await session.commit()

    # Files last: a failed rmtree must not leave the rows behind, and a client
    # who left should not leave their extracts on disk.
    for path in (
        Path(settings.uploads_dir) / str(portfolio_id),
        Path(settings.artifacts_dir) / "user_specs" / str(portfolio_id),
        *(Path(p) for p in artifacts if p),
    ):
        shutil.rmtree(path, ignore_errors=True)
    return Response(status_code=204)
