"""portfolio sleeves

Revision ID: b71c3e5a9f42
Revises: f65acdbe0694
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import fastapi_users_db_sqlalchemy


revision: str = 'b71c3e5a9f42'
down_revision: Union[str, None] = 'f65acdbe0694'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('portfolio_sleeves',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('strategy_ref', sa.Text(), nullable=False),
    sa.Column('target_pct', sa.Numeric(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'strategy_ref', name='uq_sleeves_tenant_strategy')
    )
    op.create_index(op.f('ix_portfolio_sleeves_tenant_id'), 'portfolio_sleeves', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_portfolio_sleeves_tenant_id'), table_name='portfolio_sleeves')
    op.drop_table('portfolio_sleeves')
