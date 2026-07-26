"""tax_summaries

Cache for `tlh_plan --json` with no sells — the book's current tax situation
(isenção headroom, prejuízo a compensar, IR devido, harvest candidates).

Cached because producing it dumps the tenant's whole trade history to CSV and
shells into taxcore. Portfólio reads it on load and must never wait on that.

Tenant-scoped, so the LGPD export and erasure sweeps pick it up automatically —
they select tables by the presence of a tenant_id column.

Revision ID: c9a4e7b21d35
Revises: b8f1c04d29a7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy.dialects import postgresql

revision: str = 'c9a4e7b21d35'
down_revision: Union[str, None] = 'b8f1c04d29a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tax_summaries',
        # One row per tenant: the primary key IS the tenant, so a recompute is an
        # upsert and there is no way to accumulate two answers for one book.
        sa.Column('tenant_id', GUID(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id'),
    )


def downgrade() -> None:
    op.drop_table('tax_summaries')
