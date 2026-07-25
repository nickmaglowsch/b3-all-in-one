"""client portfolios (enterprise tier)

Revision ID: e1c7a95d3b60
Revises: a4d91b7c02e5
Create Date: 2026-07-25 00:00:00.000000

A tenant_id is now either a user.id (the account's own book) or a portfolios.id
(a client book an enterprise account manages), so the column can no longer
reference `user`. Ownership moves to tenancy.get_current_tenant, the single
place a tenant_id is chosen. Existing rows keep their tenant_id untouched — the
own book has no portfolios row, so nothing needs backfilling.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import fastapi_users_db_sqlalchemy


revision: str = 'e1c7a95d3b60'
down_revision: Union[str, None] = 'a4d91b7c02e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every table whose tenant_id pointed at user.id. Postgres' default constraint
# naming, as created by the earlier revisions.
TENANT_TABLES = (
    "asset_class_overrides",
    "backtest_runs",
    "cash_events",
    "cost_basis_overrides",
    "darf_payments",
    "imports",
    "portfolio_sleeves",
    "positions",
    "rebalance_settings",
    "trades",
    "user_strategies",
)


def upgrade() -> None:
    op.create_table('portfolios',
    sa.Column('id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('owner_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'label', name='uq_portfolios_owner_label')
    )
    op.create_index(op.f('ix_portfolios_owner_id'), 'portfolios', ['owner_id'], unique=False)

    for table in TENANT_TABLES:
        op.drop_constraint(f"{table}_tenant_id_fkey", table, type_="foreignkey")


def downgrade() -> None:
    # DESTRUCTIVE: every client book's rows are deleted, because their tenant_id
    # is no user's id and the constraint cannot be restored while they exist.
    # Rolling this back throws away the enterprise tier's whole dataset.
    #
    # The predicate is NOT-a-user rather than IS-a-portfolio on purpose: a row
    # orphaned by a job that finished after its book was deleted matches neither
    # `portfolios` nor `user`, and a single one of those would abort the whole
    # downgrade on the FK restore below.
    for table in TENANT_TABLES:
        op.execute(f'DELETE FROM {table} WHERE tenant_id NOT IN (SELECT id FROM "user")')
        op.create_foreign_key(
            f"{table}_tenant_id_fkey", table, "user", ["tenant_id"], ["id"]
        )

    op.drop_index(op.f('ix_portfolios_owner_id'), table_name='portfolios')
    op.drop_table('portfolios')
