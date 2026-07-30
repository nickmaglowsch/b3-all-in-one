"""entitlement por módulo: user.modules substitui user.plan

A escada (gratis < investidor < trader < enterprise) vira um conjunto de módulos
independentes. O backfill preserva o acesso de quem já paga: cada tier antigo se
traduz nos módulos que ele abria, e `enterprise` vira o bundle `empresarial` (que
billing.entitled expande para todos).

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

# tier antigo -> módulos novos. `investidor` abria IRPF, TLH/simulação (mercado) e
# o índice da carteira; `trader` somava o bench.
BACKFILL = {
    'gratis': [],
    'investidor': ['indice', 'irpf', 'mercado'],
    'trader': ['bench', 'indice', 'irpf', 'mercado'],
    'enterprise': ['empresarial'],
}
# E o caminho de volta: o menor tier que cobre o conjunto, para um downgrade não
# tirar acesso de quem paga.
ROLLBACK = [
    ('enterprise', 'empresarial'),
    ('trader', 'bench'),
]


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
    for plan, modules in BACKFILL.items():
        if not modules:
            continue
        op.execute(
            sa.text("UPDATE \"user\" SET modules = :mods WHERE plan = :plan").bindparams(
                sa.bindparam('mods', value=modules, type_=postgresql.JSONB()),
                sa.bindparam('plan', value=plan),
            )
        )
    op.drop_column('user', 'plan')


def downgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'plan',
            sa.String(),
            server_default=sa.text("'gratis'"),
            nullable=False,
        ),
    )
    # Do mais alto para o mais baixo: quem tem `empresarial` não deve virar trader.
    for plan, module in ROLLBACK:
        op.execute(
            sa.text(
                "UPDATE \"user\" SET plan = :plan "
                "WHERE plan = 'gratis' AND modules @> :needle"
            ).bindparams(
                sa.bindparam('plan', value=plan),
                sa.bindparam('needle', value=[module], type_=postgresql.JSONB()),
            )
        )
    op.execute(
        sa.text(
            "UPDATE \"user\" SET plan = 'investidor' "
            "WHERE plan = 'gratis' AND jsonb_array_length(modules) > 0"
        )
    )
    op.drop_column('user', 'modules')
