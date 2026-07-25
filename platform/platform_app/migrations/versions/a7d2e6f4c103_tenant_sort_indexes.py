"""tenant sort indexes

Every tenant table was indexed on ``tenant_id`` alone, but no read is ever just
a tenant filter — each one also sorts:

    trades         WHERE tenant_id ORDER BY date DESC, id DESC LIMIT/OFFSET
    backtest_runs  WHERE tenant_id ORDER BY created_at DESC
    imports        WHERE tenant_id ORDER BY created_at
    positions      WHERE tenant_id ORDER BY ticker

With the single-column index Postgres reads every row the tenant owns and sorts
it to return a page. Measured on 500k synthetic rows across 200 tenants, the
paginated trades query goes 18.55 ms -> 0.11 ms once the sort key is in the
index. Today's books are small enough that it does not show; a decade of history
across an escritório's client list is exactly the shape that makes it show.

The single-column indexes are dropped rather than kept: tenant_id leads each
composite, so every plain ``WHERE tenant_id = ?`` lookup (and the wholesale
DELETE the import rebuild does) is still served, and the import job stops paying
to maintain two indexes where one does.

Revision ID: a7d2e6f4c103
Revises: e1c7a95d3b60
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7d2e6f4c103'
down_revision: Union[str, None] = 'e1c7a95d3b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, single-column index to drop, composite name, composite columns).
# sa.text for the descending keys — a plain "date DESC" string would be quoted as
# a column name of that literal text.
_INDEXES = [
    ('trades', 'ix_trades_tenant_id', 'ix_trades_tenant_date',
     ['tenant_id', sa.text('date DESC'), sa.text('id DESC')]),
    ('backtest_runs', 'ix_backtest_runs_tenant_id', 'ix_backtest_runs_tenant_created',
     ['tenant_id', sa.text('created_at DESC')]),
    ('imports', 'ix_imports_tenant_id', 'ix_imports_tenant_created',
     ['tenant_id', 'created_at']),
    ('positions', 'ix_positions_tenant_id', 'ix_positions_tenant_ticker',
     ['tenant_id', 'ticker']),
]


def upgrade() -> None:
    for table, single, composite, cols in _INDEXES:
        op.create_index(composite, table, cols, unique=False)
        op.drop_index(single, table_name=table)


def downgrade() -> None:
    for table, single, composite, _ in _INDEXES:
        op.create_index(single, table, ['tenant_id'], unique=False)
        op.drop_index(composite, table_name=table)
