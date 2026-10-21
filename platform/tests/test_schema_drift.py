"""The migrated schema and the erasure enumeration must name the same tables.

privacy._tenant_tables() finds what to export and what to erase by walking
``Base.metadata`` for a ``tenant_id`` column — i.e. the *models*. The database is
built by the *migrations*. Nothing forces those two to agree, and the test suite
cannot notice: conftest builds its DB with ``Base.metadata.create_all``, so a
table that exists only in the migration chain is invisible to every other test.

That gap is an LGPD hole, not a tidiness problem. A tenant-scoped table present
in the database but absent from the models is one that "excluir minha conta"
walks straight past — it would answer *eliminado* over rows it never touched.
env.py's ``include_name`` makes it permanent: autogenerate only manages tables
already in the metadata, so a table dropped from the models is never proposed
for a drop again. It just sits there, uncovered, forever.

So: migrate a scratch database and compare it against the enumeration.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import ADMIN_DSN, _admin

MIGRATED_DB = "platform_migration_check"
PLATFORM_DIR = Path(__file__).resolve().parents[1]

# Not ours: procrastinate ships its own tables into the same database, and
# alembic never manages them.
_FOREIGN_PREFIXES = ("procrastinate_", "alembic_")


@pytest_asyncio.fixture(loop_scope="session")
async def migrated_tenant_tables() -> set[str]:
    """Tenant-scoped table names in a database built by `alembic upgrade head`."""
    await _admin(f"DROP DATABASE IF EXISTS {MIGRATED_DB} WITH (FORCE)")
    await _admin(f"CREATE DATABASE {MIGRATED_DB}")
    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{MIGRATED_DB}"
    try:
        # Subprocess, not alembic's Python API: settings is lru_cached and already
        # bound to the test DB by conftest's import-time env, so the only honest
        # way to point env.py somewhere else is a fresh interpreter — which is
        # also exactly what docker-entrypoint.sh does in production.
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PLATFORM_DIR,
            env={**os.environ, "DATABASE_URL": dsn},
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            pytest.fail(f"alembic upgrade head failed:\n{proc.stderr[-3000:]}")

        engine = create_async_engine(dsn)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text("""
                    SELECT table_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND column_name = 'tenant_id'
                """))
                names = {r[0] for r in rows}
        finally:
            await engine.dispose()
        yield {n for n in names if not n.startswith(_FOREIGN_PREFIXES)}
    finally:
        await _admin(f"DROP DATABASE IF EXISTS {MIGRATED_DB} WITH (FORCE)")


async def test_erasure_covers_every_tenant_table_the_migrations_create(
    migrated_tenant_tables,
):
    from platform_app.privacy import _tenant_tables

    enumerated = {t.name for t in _tenant_tables()}
    uncovered = migrated_tenant_tables - enumerated

    assert not uncovered, (
        f"tenant-scoped tables exist in the migrated database that privacy's "
        f"export/erasure never walks: {sorted(uncovered)}. Either model them so "
        f"Base.metadata carries them, or drop them in a migration — leaving a "
        f"tenant table unmodelled means 'excluir minha conta' silently skips it."
    )


async def test_no_modelled_tenant_table_is_missing_from_the_migrations(
    migrated_tenant_tables,
):
    """The other direction: a model with no migration means erasure would query a
    table that isn't there, and the whole request 500s instead of erasing."""
    from platform_app.privacy import _tenant_tables

    enumerated = {t.name for t in _tenant_tables()}
    missing = enumerated - migrated_tenant_tables

    assert not missing, (
        f"models declare tenant tables the migration chain never creates: "
        f"{sorted(missing)}. Add the migration."
    )


DRIFT_DB = "platform_alembic_drift_check"


def _alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PLATFORM_DIR, env={**os.environ, "DATABASE_URL": dsn},
        capture_output=True, text=True, timeout=180,
    )


async def test_alembic_check_is_clean_on_a_migrated_database():
    """The tests above compare table *names*. A constraint can drift too, and
    did: b4d2f81c93ae gave renda_fixa_marks a tenant_id -> user.id FK the model
    never declared, so every posição report imported into a client book died on
    the insert in production while the create_all-built suite stayed green.
    `alembic check` diffs the whole migrated schema against the models."""
    await _admin(f"DROP DATABASE IF EXISTS {DRIFT_DB} WITH (FORCE)")
    await _admin(f"CREATE DATABASE {DRIFT_DB}")
    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{DRIFT_DB}"
    try:
        up = _alembic(dsn, "upgrade", "head")
        assert up.returncode == 0, f"alembic upgrade head failed:\n{up.stderr[-3000:]}"
        check = _alembic(dsn, "check")
        assert check.returncode == 0, (
            "the migrated schema and the models disagree — add a migration:\n"
            f"{(check.stdout + check.stderr)[-3000:]}")
    finally:
        await _admin(f"DROP DATABASE IF EXISTS {DRIFT_DB} WITH (FORCE)")
