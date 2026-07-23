"""user strategies (spec/06)

Revision ID: c7e2f4a9b1d8
Revises: d3f8a1c2b4e6
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import fastapi_users_db_sqlalchemy
from sqlalchemy.dialects import postgresql


revision: str = 'c7e2f4a9b1d8'
down_revision: Union[str, None] = 'd3f8a1c2b4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_strategies',
    sa.Column('id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'name', name='uq_user_strategies_tenant_name')
    )
    op.create_index(op.f('ix_user_strategies_tenant_id'), 'user_strategies', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_strategies_tenant_id'), table_name='user_strategies')
    op.drop_table('user_strategies')
