"""mcp_tokens + user_strategies.origin (spec/09: strategies authored by an AI)

Revision ID: e8b3d6a41f27
Revises: 9d4e2b7c1f05
Create Date: 2026-10-23 00:00:00.000000

mcp_tokens carries no tenant_id -> user.id foreign key, deliberately: a
tenant_id can be a portfolios.id (see e1c7a95d3b60 and 9d4e2b7c1f05).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import fastapi_users_db_sqlalchemy


revision: str = 'e8b3d6a41f27'
down_revision: Union[str, None] = '9d4e2b7c1f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('mcp_tokens',
    sa.Column('id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('prefix', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_mcp_tokens_tenant_id'), 'mcp_tokens', ['tenant_id'], unique=False)
    op.add_column('user_strategies', sa.Column('origin', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_strategies', 'origin')
    op.drop_index(op.f('ix_mcp_tokens_tenant_id'), table_name='mcp_tokens')
    op.drop_table('mcp_tokens')
