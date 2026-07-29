"""darf_payments — the DARF ledger (spec/03, spec/04 Phase 2)

Recreates the table 8083522f305e created and d5b8e37f1a94 dropped, in the
narrower shape the ledger actually needs. The dropped version mirrored
taxcore/data/darf_payments.csv column for column, which meant storing the
apuração itself — vendas_brutas, pnl_liquido, prejuizo_compensado, base_calculo,
aliquota. Those are recomputed from the trade history on every read (taxcore's
`irpf_2026 --json`, the same function the IRPF report renders from), so keeping
a copy here would be a second, staler answer to the one question that has to be
right. What is left is only what the tenant knows and the engine cannot: that a
DARF was paid, for how much, on what day.

Modelled this time, so privacy._tenant_tables() walks it for the LGPD export and
erasure — which is exactly why the unmodelled one was dropped instead of kept.

Revision ID: f2a6c0d84b19
Revises: b4d2f81c93ae
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision: str = 'f2a6c0d84b19'
down_revision: Union[str, None] = 'b4d2f81c93ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'darf_payments',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', GUID(), nullable=False),
        # First day of the apuração month, so the year filter is a range scan.
        sa.Column('apuracao', sa.Date(), nullable=False),
        sa.Column('classe', sa.Text(), nullable=False),
        sa.Column('valor_principal', sa.Numeric(), nullable=False),
        sa.Column('data_pagamento', sa.Date(), nullable=True),
        sa.Column('obs', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        # Ações and FIIs are separate apurações with separate rates; one DARF each
        # per month, and a second row for the same pair is a data-entry mistake.
        sa.UniqueConstraint('tenant_id', 'apuracao', 'classe',
                            name='uq_darf_tenant_apuracao_classe'),
    )
    op.create_index(op.f('ix_darf_payments_tenant_id'), 'darf_payments',
                    ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_darf_payments_tenant_id'), table_name='darf_payments')
    op.drop_table('darf_payments')
