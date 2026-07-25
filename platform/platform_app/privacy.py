"""LGPD (Lei 13.709/2018) data-subject rights, served by the product itself.

Art. 18 gives the titular confirmação do tratamento (I), acesso (II),
portabilidade in a machine-readable form (V) and eliminação (VI). Rather than a
mailbox someone answers within 15 days, each is a route:

    GET    /privacy/summary   what we hold, per book, in counts
    GET    /privacy/export    all of it, as JSON
    DELETE /privacy/account   none of it, ever again

The **account** is the unit, not the tenant: an escritório's own book and every
client book it manages leave together, because "excluir minha conta" that left a
client's trades on disk would be a lie. The tables are read from the metadata,
never a hand-kept list, so a tenant table added tomorrow is exported and erased
without anyone remembering this file exists.
"""
from __future__ import annotations

import datetime
import decimal
import logging
import uuid

import stripe
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.billing import effective_plan
from platform_app.config import settings
from platform_app.db import Base, get_async_session
from platform_app.models import OAuthAccount, Portfolio, User
from platform_app.routes_portfolios import (
    SELF_LABEL,
    purge_tenant_files,
    purge_tenant_rows,
)
from platform_app.users import current_active_user

log = logging.getLogger(__name__)

privacy_router = APIRouter(prefix="/privacy", tags=["privacy"])

# The encarregado's channel (Art. 41), for the rights these routes can't serve —
# correção, oposição, revisão de decisão automatizada. The privacy policy page
# publishes the same address.
CONTACT = "privacidade@quantax.com.br"

# Ours, not the titular's: an export naming them would publish the server's
# filesystem layout and nothing about the person asking. `error` is in here for
# the same reason — it carries the tail of a subprocess stderr, so a deliberately
# corrupt .xlsx would otherwise turn the export into a map of the deploy. The
# row's `status` still says the job failed, which is the part that is about them.
_INTERNAL_COLUMNS = {"stored_path", "artifacts_path", "error"}


def _tenant_tables() -> list:
    return [t for t in Base.metadata.sorted_tables if "tenant_id" in t.c]


def _jsonable(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def _account(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "plan": effective_plan(user),
        "billed_plan": user.plan,
        "plan_status": user.plan_status,
        "plan_renews_at": user.plan_renews_at.isoformat() if user.plan_renews_at else None,
        # The identifier itself lives at Stripe, the operator that has to keep it
        # for invoicing; copying it here would only spread it further.
        "has_stripe_customer": bool(user.stripe_customer_id),
        "oauth_providers": sorted(
            {a.oauth_name for a in (user.oauth_accounts or [])}
        ),
    }


async def _books(session: AsyncSession, user: User) -> list[tuple[uuid.UUID, str, bool]]:
    """(tenant_id, label, is_self) for every book the account answers for — its
    own first, then the managed ones. Mirrors GET /portfolios."""
    rows = (
        await session.scalars(
            select(Portfolio).where(Portfolio.owner_id == user.id).order_by(Portfolio.label)
        )
    ).all()
    return [(user.id, SELF_LABEL, True), *((p.id, p.label, False) for p in rows)]


@privacy_router.get("/summary")
async def privacy_summary(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Art. 18 I/II — confirmation that processing exists, and of what.

    Identity-scoped like /billing: this answers for the whole account, so it
    ignores the acting-book header instead of reporting one book of several.
    """
    books = []
    for tenant_id, label, is_self in await _books(session, user):
        # ponytail: one count query per table per book (~13 × books). This is a
        # human opening a page, not a hot path; batch it if a firm's book list
        # ever makes it slow.
        records = {
            table.name: await session.scalar(
                select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)
            )
            for table in _tenant_tables()
        }
        books.append(
            {"id": str(tenant_id), "label": label, "is_self": is_self, "records": records}
        )
    return {"account": _account(user), "books": books, "contact": CONTACT}


@privacy_router.get("/export")
async def privacy_export(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> JSONResponse:
    """Art. 18 V — access and portability, "em formato que permita a utilização
    subsequente": one JSON file with every row of every book, downloaded rather
    than rendered."""
    # ponytail: the whole book is materialized in memory. A decade of B3 history
    # is ~10^5 rows ≈ tens of MB — fine for a page a person opens once. Stream it
    # (or paginate by table) if a book ever gets big enough to matter.
    books = []
    for tenant_id, label, is_self in await _books(session, user):
        tables = {}
        for table in _tenant_tables():
            rows = (
                await session.execute(select(table).where(table.c.tenant_id == tenant_id))
            ).mappings().all()
            tables[table.name] = [
                {k: _jsonable(v) for k, v in row.items() if k not in _INTERNAL_COLUMNS}
                for row in rows
            ]
        books.append(
            {"id": str(tenant_id), "label": label, "is_self": is_self, "tables": tables}
        )
    payload = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "account": _account(user),
        "books": books,
        "contact": CONTACT,
    }
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": 'attachment; filename="quantax-meus-dados.json"'
        },
    )


async def _forget_at_stripe(customer_id: str) -> None:
    """Ask the operator to forget the customer too — which also cancels whatever
    it was billing, so an erased account can't keep paying. Stripe keeps the
    invoices it must keep for fiscal retention; that is its obligation, not ours.

    Best-effort by design: the right to erasure cannot be held hostage by a
    third party's downtime, so a failure here never blocks the deletion.
    """
    if not (customer_id and settings.stripe_secret_key):
        return
    try:
        await stripe.Customer.delete_async(customer_id)
    except Exception:  # noqa: BLE001 — see docstring
        # The user row is already gone, so this id now exists nowhere else: if it
        # isn't in the log, nobody can ever reconcile the subscription that is
        # still billing a card for an account that no longer exists.
        log.exception("stripe customer %s survived account erasure", customer_id)


@privacy_router.delete("/account")
async def delete_account(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Art. 18 VI — eliminação. Books and the login itself in one transaction,
    then the files they named (see purge_tenant_files for why in that order).

    A hard delete, not an anonymized tombstone: nothing in this schema keeps a
    non-null FK to the login once its books are gone, and the records a
    controller must retain for accounting live at Stripe (the operator), not
    here. Deleting the row for real also frees the email to sign up again.
    """
    books = await _books(session, user)
    artifacts = {
        tenant_id: await purge_tenant_rows(session, tenant_id) for tenant_id, *_ in books
    }
    await session.execute(delete(Portfolio).where(Portfolio.owner_id == user.id))
    # Core deletes, not session.delete(user): the oauth_accounts relationship is
    # eagerly loaded, and the ORM would try to null its NOT NULL user_id instead
    # of letting the rows go.
    await session.execute(delete(OAuthAccount).where(OAuthAccount.user_id == user.id))
    customer_id = user.stripe_customer_id
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()

    for tenant_id, paths in artifacts.items():
        purge_tenant_files(tenant_id, paths)
    await _forget_at_stripe(customer_id)

    return {
        "status": "eliminado",
        "books": [str(tenant_id) for tenant_id, *_ in books],
        "contact": CONTACT,
    }
