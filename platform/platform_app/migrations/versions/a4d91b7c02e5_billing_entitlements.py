"""billing entitlements on the tenant row (spec/01)

Revision ID: a4d91b7c02e5
Revises: c7e2f4a9b1d8
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4d91b7c02e5'
down_revision: Union[str, None] = 'c7e2f4a9b1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('stripe_customer_id', sa.String(), nullable=True))
    op.add_column('user', sa.Column('plan', sa.String(), server_default=sa.text("'gratis'"), nullable=False))
    op.add_column('user', sa.Column('plan_status', sa.String(), nullable=True))
    op.add_column('user', sa.Column('plan_renews_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user', sa.Column('plan_event_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_user_stripe_customer_id'), 'user', ['stripe_customer_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_stripe_customer_id'), table_name='user')
    op.drop_column('user', 'plan_event_at')
    op.drop_column('user', 'plan_renews_at')
    op.drop_column('user', 'plan_status')
    op.drop_column('user', 'plan')
    op.drop_column('user', 'stripe_customer_id')
