"""renda_fixa_marks: issuer prices read off the posição report

Revision ID: b4d2f81c93ae
Revises: a3f1c7d92b40
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import fastapi_users_db_sqlalchemy


revision: str = 'b4d2f81c93ae'
down_revision: Union[str, None] = 'a3f1c7d92b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('renda_fixa_marks',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('ticker', sa.Text(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('price', sa.Numeric(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'ticker', 'date', name='uq_rf_marks_tenant_ticker_date')
    )
    op.create_index(op.f('ix_renda_fixa_marks_tenant_id'), 'renda_fixa_marks', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_renda_fixa_marks_tenant_id'), table_name='renda_fixa_marks')
    op.drop_table('renda_fixa_marks')
