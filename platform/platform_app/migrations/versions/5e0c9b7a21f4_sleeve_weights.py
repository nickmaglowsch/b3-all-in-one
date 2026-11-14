"""sleeve_weights

Each strategy sleeve's latest advised weights, one row per family (a strategy at
a cadence; plus tenant and edit stamp for a user strategy). Shared by every API
process and the ingest worker that refreshes it — see platform_app/sleeve_weights.py.

A user strategy's rows carry its tenant_id, so the LGPD export and erasure
sweeps pick them up; a built-in's are the same for everyone and carry none.

Revision ID: 5e0c9b7a21f4
Revises: faca3d289b5f
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy.dialects import postgresql

revision: str = '5e0c9b7a21f4'
down_revision: Union[str, None] = 'faca3d289b5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sleeve_weights',
        sa.Column('id', GUID(), nullable=False),
        sa.Column('family', sa.Text(), nullable=False),
        sa.Column('tenant_id', GUID(), nullable=True),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('plane', sa.Text(), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('family'),
    )
    op.create_index('ix_sleeve_weights_tenant_id', 'sleeve_weights', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_sleeve_weights_tenant_id', table_name='sleeve_weights')
    op.drop_table('sleeve_weights')
