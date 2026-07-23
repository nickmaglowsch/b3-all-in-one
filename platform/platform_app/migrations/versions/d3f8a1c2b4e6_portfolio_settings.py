"""portfolio settings: asset_class_overrides + rebalance_settings

Revision ID: d3f8a1c2b4e6
Revises: b71c3e5a9f42
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import fastapi_users_db_sqlalchemy


revision: str = 'd3f8a1c2b4e6'
down_revision: Union[str, None] = 'b71c3e5a9f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('asset_class_overrides',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('ticker', sa.Text(), nullable=False),
    sa.Column('treatment', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'ticker', name='uq_asset_override_tenant_ticker')
    )
    op.create_index(op.f('ix_asset_class_overrides_tenant_id'), 'asset_class_overrides', ['tenant_id'], unique=False)

    op.create_table('rebalance_settings',
    sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('buy_only_laggards', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('sell_only_for_harvest', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('cadence', sa.Text(), server_default=sa.text("'ME'"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('tenant_id')
    )


def downgrade() -> None:
    op.drop_table('rebalance_settings')
    op.drop_index(op.f('ix_asset_class_overrides_tenant_id'), table_name='asset_class_overrides')
    op.drop_table('asset_class_overrides')
