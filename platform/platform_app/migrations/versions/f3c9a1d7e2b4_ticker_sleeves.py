"""ticker sleeves: a live sleeve can hold one listed instrument directly

Revision ID: f3c9a1d7e2b4
Revises: e8b3d6a41f27
Create Date: 2026-11-13 00:00:00.000000

A live sleeve was a strategy_ref (a strategy or CASH_CDI). It can now be a
ticker on a market plane instead — PETR4 on B3, SPY on US, BTCUSDT on CRYPTO —
so strategy_ref becomes nullable and exactly one of the two is set. Additive:
every existing row is a strategy row and satisfies the check as it stands.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3c9a1d7e2b4'
down_revision: Union[str, None] = 'e8b3d6a41f27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('portfolio_sleeves', sa.Column('ticker', sa.Text(), nullable=True))
    op.add_column('portfolio_sleeves', sa.Column('market', sa.Text(), nullable=True))
    op.alter_column('portfolio_sleeves', 'strategy_ref', existing_type=sa.Text(),
                    nullable=True)
    op.create_unique_constraint('uq_sleeves_tenant_ticker', 'portfolio_sleeves',
                                ['tenant_id', 'market', 'ticker'])
    # Exactly one of strategy_ref / ticker; a ticker always with its market, and
    # a market the planes have (a NULL market passes IN, as a strategy row needs).
    op.create_check_constraint(
        'ck_sleeves_one_source', 'portfolio_sleeves',
        "(strategy_ref IS NULL) <> (ticker IS NULL) AND (ticker IS NULL) = "
        "(market IS NULL) AND market IN ('B3', 'US', 'CRYPTO')")


def downgrade() -> None:
    # A ticker sleeve has no strategy_ref to fall back to; it cannot survive the
    # NOT NULL coming back, so the downgrade drops those rows (the tenant's live
    # target loses them, as it would have refused them before this revision).
    op.execute('DELETE FROM portfolio_sleeves WHERE ticker IS NOT NULL')
    op.drop_constraint('ck_sleeves_one_source', 'portfolio_sleeves', type_='check')
    op.drop_constraint('uq_sleeves_tenant_ticker', 'portfolio_sleeves', type_='unique')
    op.alter_column('portfolio_sleeves', 'strategy_ref', existing_type=sa.Text(),
                    nullable=False)
    op.drop_column('portfolio_sleeves', 'market')
    op.drop_column('portfolio_sleeves', 'ticker')
