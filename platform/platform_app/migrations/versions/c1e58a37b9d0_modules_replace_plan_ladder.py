"""entitlement por módulo: user.modules substitui user.plan

A escada (gratis < investidor < trader < enterprise) vira um conjunto de módulos
independentes, mais `stripe_subscription_id` — a assinatura viva que
PUT /billing/modules altera e que o webhook usa para ignorar uma segunda.

Sem backfill de propósito. Traduzir os tiers antigos em módulos parece a coisa
segura a fazer, mas seria proteger um usuário que não existe: `.env.prod.example`
nasceu com `STRIPE_PRICE_INVESTIDOR=`/`TRADER=` vazios e nunca recebeu um id, e
`billing._validate_cart` recusa (400) um módulo sem preço configurado — então
nenhuma assinatura foi criada em produção. Os ids de preço antigos também não
existem mais em `config.py`, logo um `plan` traduzido aqui não teria como ser
reconfirmado pelo webhook depois. Um backfill que não pode disparar é pior que
nenhum: afirma uma garantia que ninguém testou.

Se algum banco de dev tiver um tier pago, `POST /billing/dev-modules` reescreve o
conjunto (é o que `scripts/dev_seed.sh` faz a cada execução).

Revision ID: c1e58a37b9d0
Revises: f2a6c0d84b19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c1e58a37b9d0'
down_revision: Union[str, None] = 'f2a6c0d84b19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'modules',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column('user', sa.Column('stripe_subscription_id', sa.String(), nullable=True))
    op.drop_column('user', 'plan')


def downgrade() -> None:
    # Volta ao default: o mapa inverso módulos->tier seria uma invenção, já que a
    # subida não guardou de onde ninguém veio.
    op.add_column(
        'user',
        sa.Column(
            'plan',
            sa.String(),
            server_default=sa.text("'gratis'"),
            nullable=False,
        ),
    )
    op.drop_column('user', 'stripe_subscription_id')
    op.drop_column('user', 'modules')
