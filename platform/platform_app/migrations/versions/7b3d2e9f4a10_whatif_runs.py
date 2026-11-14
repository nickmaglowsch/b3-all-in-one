"""whatif_runs

POST /whatif replayed up to four full backtests inside the request (6-20 s
each). It is a worker job now, and this is its row: the page polls
GET /whatif/{id}. Tenant-scoped, so the LGPD sweeps pick it up.

Revision ID: 7b3d2e9f4a10
Revises: 5e0c9b7a21f4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy.dialects import postgresql

revision: str = '7b3d2e9f4a10'
down_revision: Union[str, None] = '5e0c9b7a21f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'whatif_runs',
        sa.Column('id', GUID(), nullable=False),
        sa.Column('tenant_id', GUID(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'running', 'done', 'error')",
                           name='ck_whatif_runs_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_whatif_runs_tenant_created', 'whatif_runs',
                    ['tenant_id', sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_whatif_runs_tenant_created', table_name='whatif_runs')
    op.drop_table('whatif_runs')
