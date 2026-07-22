import uuid

from fastapi import Depends

from platform_app.models import User
from platform_app.users import current_active_user


async def get_current_tenant(
    user: User = Depends(current_active_user),
) -> tuple[User, uuid.UUID]:
    """Yield the authenticated user and its tenant_id.

    tenant_id == user.id for now (one user = one tenant). Scope every tenant
    query by the returned tenant_id — never trust an id from the request body.

    RLS hook: when tenant tables land, set the Postgres session GUC here
    (`SET LOCAL app.tenant_id = <tenant_id>`) on the request's DB session so
    row-level-security policies enforce isolation in the database itself.
    """
    return user, user.id
