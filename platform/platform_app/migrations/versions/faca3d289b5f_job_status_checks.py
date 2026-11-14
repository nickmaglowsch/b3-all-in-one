"""job status checks: backtest_runs, imports and ingest_runs admit only their states

Revision ID: faca3d289b5f
Revises: f3c9a1d7e2b4
Create Date: 2026-11-14 00:00:00.000000

The three job tables kept their state machine in a bare String column, and the
statuses were literals scattered across routes, tasks and the worker's boot
sweep. platform_app.jobstatus now names them; this makes the database refuse
anything else, so a typo in a status is an IntegrityError at the write rather
than a row every reader quietly fails to match (and that therefore never leaves
the quota count, the boot sweep or the listing's polling loop).

The lists are spelled out rather than imported from jobstatus: a migration is a
record of the schema at its revision and must not move when the app does.

Every value the code has ever written is in its list, so on a healthy database
the UPDATEs below touch nothing. They exist so the deploy cannot fail on a row
some hand edit left in a state nothing handles; `error` is the one terminal
state every reader already treats as "finished, look at it".
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'faca3d289b5f'
down_revision: Union[str, None] = 'f3c9a1d7e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECKS = (
    ('ck_backtest_runs_status', 'backtest_runs', ('pending', 'running', 'done', 'error')),
    ('ck_imports_status', 'imports', ('pending', 'processing', 'done', 'error')),
    ('ck_ingest_runs_status', 'ingest_runs', ('running', 'done', 'error')),
)


def _in(statuses: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")"


def upgrade() -> None:
    for name, table, statuses in _CHECKS:
        op.execute(f"UPDATE {table} SET status = 'error' WHERE NOT ({_in(statuses)})")
        op.create_check_constraint(name, table, _in(statuses))


def downgrade() -> None:
    for name, table, _ in _CHECKS:
        op.drop_constraint(name, table, type_='check')
