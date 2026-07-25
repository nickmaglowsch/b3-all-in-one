import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.db import get_async_session
from platform_app.models import Portfolio, User
from platform_app.users import current_active_user, current_optional_user

# Which book the request acts on. Absent => the caller's own, i.e. every existing
# client keeps working untouched.
PORTFOLIO_HEADER = "X-Portfolio-Id"


async def _acting_tenant(
    user: User, request: Request, session: AsyncSession
) -> uuid.UUID:
    """Resolve the header to a tenant_id the caller is allowed to act on.

    This is the only place a tenant_id is chosen, so it is also the only place
    ownership and entitlement need checking — everything downstream just reads
    the returned id.
    """
    raw = request.headers.get(PORTFOLIO_HEADER)
    if not raw:
        return user.id
    try:
        portfolio_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(400, f"{PORTFOLIO_HEADER} inválido: {raw}") from None
    if portfolio_id == user.id:
        return user.id

    owned = await session.scalar(
        select(Portfolio.id).where(
            Portfolio.id == portfolio_id, Portfolio.owner_id == user.id
        )
    )
    # 404, not 403: whether someone else's portfolio exists is not the caller's
    # business.
    if owned is None:
        raise HTTPException(404, "carteira não encontrada")

    # Local import: billing depends on this module for get_current_tenant.
    from platform_app.billing import ENTERPRISE, effective_plan

    current = effective_plan(user)
    if current != ENTERPRISE:
        # The books survive a downgrade; acting on them is what the tier sells.
        raise HTTPException(
            402,
            detail={
                "error": "plan_required",
                "required_plan": ENTERPRISE,
                "current_plan": current,
            },
        )
    return portfolio_id


async def get_optional_tenant(
    request: Request,
    user: User | None = Depends(current_optional_user),
    session: AsyncSession = Depends(get_async_session),
) -> tuple[User, uuid.UUID] | None:
    """Same as get_current_tenant but returns None for anonymous requests, so a
    public endpoint (GET /strategies) can serve built-ins to anon and the merged
    gallery to an authed tenant."""
    if user is None:
        return None
    return user, await _acting_tenant(user, request, session)


async def get_current_tenant(
    request: Request,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> tuple[User, uuid.UUID]:
    """Yield the authenticated user and the tenant_id it is acting as.

    The user is *who you are* (identity, billing); the tenant_id is *whose book
    you are touching* — its own by default, or a managed client portfolio the
    enterprise tier selects with the X-Portfolio-Id header. Scope every tenant
    query by the returned tenant_id — never trust an id from the request body.

    RLS hook: when tenant tables land, set the Postgres session GUC here
    (`SET LOCAL app.tenant_id = <tenant_id>`) on the request's DB session so
    row-level-security policies enforce isolation in the database itself.
    """
    return user, await _acting_tenant(user, request, session)
