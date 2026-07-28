"""renda fixa terms

The indexer of a still-open contract (IPCA+x%, %CDI, CDI+x%, prefixado). Neither
B3 extract carries it, and a contract that has not been redeemed has only one
mark — so there is nothing to imply the rate from. Tenant-entered, one row per
contract, and only needed while the contract is open.

Revision ID: a3f1c7d92b40
Revises: d5b8e37f1a94
Create Date: 2026-07-27

"""
from typing import Sequence, Union

import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = 'a3f1c7d92b40'
down_revision: Union[str, None] = 'd5b8e37f1a94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'renda_fixa_terms',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
        sa.Column('ticker', sa.Text(), nullable=False),
        sa.Column('indexer', sa.Text(), nullable=False),
        sa.Column('rate_pct', sa.Numeric(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'ticker', name='uq_rf_terms_tenant_ticker'),
    )
    op.create_index(op.f('ix_renda_fixa_terms_tenant_id'), 'renda_fixa_terms',
                    ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_renda_fixa_terms_tenant_id'), table_name='renda_fixa_terms')
    op.drop_table('renda_fixa_terms')
