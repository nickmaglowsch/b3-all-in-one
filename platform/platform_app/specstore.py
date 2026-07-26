"""Materialize a tenant's user_strategies rows into a scratch dir the engine
subprocess discovers via ENGINE_USER_SPECS_DIR (spec/06).

JSON is valid YAML, so each spec is written as ``json.dumps(spec)`` with a
``.yaml`` name — no pyyaml dep in the platform venv. Every spec is rewritten per
call and ids that vanished are swept, so there is no staleness to reason about.
Filenames are the row id, never the user's name.

Lives in its own module (not routes/enginerun) so both the request path and the
worker (tasks.py) can import it without a routes↔tasks cycle.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.config import settings


def materialize_user_specs(specs: list[tuple[str, dict]], tenant_id: uuid.UUID) -> Path:
    """Write ``(id, spec)`` pairs to ``{artifacts_dir}/user_specs/{tenant_id}/{id}.yaml``
    and return the dir.

    Per-file atomic swap, never a whole-dir rmtree: the API and the worker share
    this volume and both call it, so tearing the directory down mid-call raced
    with itself — a concurrent caller's write_text hit FileNotFoundError (~17%
    of calls under 3-way concurrency) and an engine subprocess reading the dir
    saw a partial spec set. Writing each file to a temp name and os.replace-ing
    it into place means a reader only ever sees whole specs, and stale ids are
    swept afterwards so a deleted strategy still disappears.
    """
    d = Path(settings.artifacts_dir).resolve() / "user_specs" / str(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    keep = set()
    for sid, spec in specs:
        target = d / f"{sid}.yaml"
        keep.add(target.name)
        # Unique temp name: two processes materializing the same tenant must not
        # collide on the scratch file itself.
        tmp = d / f".{sid}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(spec))
        os.replace(tmp, target)
    for stale in d.glob("*.yaml"):
        if stale.name not in keep:
            stale.unlink(missing_ok=True)
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
