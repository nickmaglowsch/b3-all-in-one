"""Materialize a tenant's user_strategies rows into a scratch dir the engine
subprocess discovers via ENGINE_USER_SPECS_DIR (spec/06).

JSON is valid YAML, so each spec is written as ``json.dumps(spec)`` with a
``.yaml`` name — no pyyaml dep in the platform venv. Full rewrite per call →
zero staleness logic. Filenames are the row id, never the user's name.

Lives in its own module (not routes/enginerun) so both the request path and the
worker (tasks.py) can import it without a routes↔tasks cycle.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.config import settings


def materialize_user_specs(specs: list[tuple[str, dict]], tenant_id: uuid.UUID) -> Path:
    """Write ``(id, spec)`` pairs to ``{artifacts_dir}/user_specs/{tenant_id}/{id}.yaml``
    and return the dir. Rewrites the whole dir each call."""
    d = Path(settings.artifacts_dir).resolve() / "user_specs" / str(tenant_id)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    for sid, spec in specs:
        (d / f"{sid}.yaml").write_text(json.dumps(spec))
    return d


async def user_specs_env(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, str]:
    """``{"ENGINE_USER_SPECS_DIR": dir}`` for a tenant with any user specs, else
    ``{}`` — zero-spec tenants pass no env var (identical to today's engine calls)."""
    # ponytail: import models lazily — importing it at module top makes specstore
    # the first importer of fastapi_users.db before platform_app.db initializes it,
    # which trips a fastapi_users circular-import depending on import order.
    from platform_app.models import UserStrategy

    rows = (await session.scalars(
        select(UserStrategy).where(UserStrategy.tenant_id == tenant_id)
    )).all()
    if not rows:
        return {}
    d = materialize_user_specs([(str(r.id), r.spec) for r in rows], tenant_id)
    return {"ENGINE_USER_SPECS_DIR": str(d)}
