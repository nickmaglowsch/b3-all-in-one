"""ingest_runs

One row per scheduled market-data refresh, so a failed run is visible instead of
silent. The price plane sat seven months stale with nothing anywhere saying so —
the pipeline's only output was log lines inside a container nobody reads.

Not tenant-scoped on purpose: the market plane is shared infrastructure, not
anyone's book. That also keeps it out of the LGPD export and erasure sweeps,
which select tables by the presence of a tenant_id column.

Revision ID: b8f1c04d29a7
Revises: a7d2e6f4c103
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision: str = 'b8f1c04d29a7'
down_revision: Union[str, None] = 'a7d2e6f4c103'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ingest_runs',
        sa.Column('id', GUID(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # The only read is "latest run per kind" (GET /market/status).
    op.create_index('ix_ingest_runs_kind_started', 'ingest_runs',
                    ['kind', sa.text('started_at DESC')], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ingest_runs_kind_started', table_name='ingest_runs')
    op.drop_table('ingest_runs')
