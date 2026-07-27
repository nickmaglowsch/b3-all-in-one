"""drop the unused darf_payments table

The table was created by 8083522f305e to mirror taxcore/data/darf_payments.csv,
with the note "CRUD lands later". It never did: nothing in the codebase has ever
read or written it, in any revision.

Dropping it rather than keeping the model is the LGPD-safe direction. Erasure
and export find their tables by walking Base.metadata for a tenant_id column, so
an unmodelled tenant table is one "excluir minha conta" never touches — and
env.py's include_name means autogenerate would never again propose dropping it.
An always-empty table is harmless today, but it is a place for a named person's
DARF payments to land later, out of reach of the erasure sweep. See
tests/test_schema_drift.py, which fails if the two ever disagree again.

Revision ID: d5b8e37f1a94
Revises: c9a4e7b21d35
"""
from typing import Sequence, Union

import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "d5b8e37f1a94"
down_revision: Union[str, None] = "c9a4e7b21d35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guarded: the table is empty by construction (no writer has ever existed),
    # but a drop is one-way, so refuse rather than destroy anything a future
    # branch put there.
    rows = op.get_bind().execute(sa.text("SELECT count(*) FROM darf_payments")).scalar()
    if rows:
        raise RuntimeError(
            f"darf_payments holds {rows} rows — this migration assumes it is the "
            "never-written table from 8083522f305e. Export them before dropping."
        )
    op.drop_index(op.f("ix_darf_payments_tenant_id"), table_name="darf_payments")
    op.drop_table("darf_payments")


def downgrade() -> None:
    op.create_table(
        "darf_payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "tenant_id", fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False
        ),
        sa.Column("apuracao", sa.String(), nullable=True),
        sa.Column("codigo", sa.String(), nullable=True),
        sa.Column("classe", sa.String(), nullable=True),
        sa.Column("vendas_brutas", sa.Float(), nullable=True),
        sa.Column("pnl_liquido", sa.Float(), nullable=True),
        sa.Column("prejuizo_compensado", sa.Float(), nullable=True),
        sa.Column("base_calculo", sa.Float(), nullable=True),
        sa.Column("aliquota", sa.Float(), nullable=True),
        sa.Column("valor_principal", sa.Float(), nullable=True),
        sa.Column("vencimento", sa.Date(), nullable=True),
        sa.Column("data_pagamento", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("forma_pagamento", sa.String(), nullable=True),
        sa.Column("comprovante", sa.String(), nullable=True),
        sa.Column("relatorio", sa.String(), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        # No FK on tenant_id: e1c7a95d3b60 dropped `darf_payments_tenant_id_fkey`
        # outright (a tenant_id may now be a portfolio id, which is not a user id)
        # and never replaced it. This recreates the table as it stood at
        # c9a4e7b21d35, so e1c7's own downgrade still finds what it expects.
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_darf_payments_tenant_id"),
        "darf_payments",
        ["tenant_id"],
        unique=False,
    )
