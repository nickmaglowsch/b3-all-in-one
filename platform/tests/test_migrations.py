"""A suíte roda `Base.metadata.create_all`, então nenhum outro teste toca uma
migração. Isso deixa um buraco caro: o modelo e o alembic podem divergir e tudo
fica verde até o deploy, onde a coluna que o billing lê simplesmente não existe.

Este teste fecha o buraco pelo caminho mais curto que ainda é real — sobe um banco
descartável só com alembic, e compara as colunas da tabela `user` com o que o
modelo declara. Depois desce a migração de novo, porque uma migração que não sabe
voltar não é um rollback, é um plano de esperança.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import ADMIN_DSN, PG_NETLOC, _admin

MIGRATION_DB = "platform_alembic_check"
PLATFORM = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _alembic(*args: str, url: str) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PLATFORM,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, f"alembic {' '.join(args)} falhou:\n{out.stderr}"


async def _columns(url: str, table: str) -> dict[str, bool]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: inspect(c).get_columns(table))
    finally:
        await engine.dispose()
    return {c["name"]: c["nullable"] for c in cols}


async def test_alembic_head_matches_the_user_model():
    assert ADMIN_DSN  # importado para garantir que o conftest já apontou o DATABASE_URL
    url = f"postgresql+asyncpg://{PG_NETLOC}/{MIGRATION_DB}"
    await _admin(f"DROP DATABASE IF EXISTS {MIGRATION_DB} WITH (FORCE)")
    await _admin(f"CREATE DATABASE {MIGRATION_DB}")
    try:
        _alembic("upgrade", "head", url=url)
        migrated = await _columns(url, "user")

        from platform_app.models import User

        declared = {c.name: c.nullable for c in User.__table__.columns}
        assert migrated == declared, (
            "modelo e migração divergem em `user`: "
            f"só no banco={sorted(set(migrated) - set(declared))}, "
            f"só no modelo={sorted(set(declared) - set(migrated))}"
        )
        # A coluna que o billing inteiro lê, explicitamente: se ela sair da
        # migração, o erro tem de nomear ela e não uma diferença de conjunto.
        assert "modules" in migrated and migrated["modules"] is False
        assert "plan" not in migrated, "a escada de planos deveria ter saído do schema"

        # E sabe voltar: -1 desfaz a migração de módulos sem estourar.
        _alembic("downgrade", "-1", url=url)
        rolled_back = await _columns(url, "user")
        assert "plan" in rolled_back and "modules" not in rolled_back
    finally:
        await _admin(f"DROP DATABASE IF EXISTS {MIGRATION_DB} WITH (FORCE)")
