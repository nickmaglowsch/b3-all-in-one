import datetime
import uuid

from fastapi_users_db_sqlalchemy.generics import GUID
from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_app.db import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    # default __tablename__ = "oauth_account"
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    # default __tablename__ = "user" (reserved word, but SQLAlchemy auto-quotes it)
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(lazy="joined")

    # Billing (spec/01: entitlements on the tenant row, written by the Stripe
    # webhook only). `plan` is what was bought; access also needs plan_status —
    # see billing.effective_plan.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String, unique=True, index=True, nullable=True
    )
    plan: Mapped[str] = mapped_column(
        String, nullable=False, default="gratis", server_default=text("'gratis'")
    )
    plan_status: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_renews_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stripe fans events out with no ordering guarantee; the timestamp of the last
    # applied event lets a late `created` be dropped instead of undoing a `deleted`.
    plan_event_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Portfolio(Base):
    """A book an account manages on someone else's behalf (spec/07, enterprise tier).

    The row id IS the tenant_id the whole data plane is keyed by, so a managed
    portfolio is simply another tenant that a firm owns — no route, query or job
    below changes. The account's own book keeps tenant_id == user.id and has no
    row here, so every existing tenant stays valid without a backfill.
    """

    __tablename__ = "portfolios"
    __table_args__ = (
        # Two clients with the same name is a double-submit, not a second client.
        UniqueConstraint("owner_id", "label", name="uq_portfolios_owner_label"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("user.id"), index=True, nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Tenant data plane (Wave 2). Every table carries tenant_id. ---
# The trades/cash_events/positions rows are a deterministic full rebuild by
# taxcore's load_b3; the import job replaces them wholesale per tenant per run.


def _tenant_col(index: bool = True) -> Mapped[uuid.UUID]:
    # ponytail: no FK — a tenant_id is either a user.id (own book) or a
    # portfolios.id (managed), and one column can't reference both. Ownership is
    # enforced in tenancy.get_current_tenant, the single door to a tenant_id.
    # Upgrade path: give every account a portfolios row and point the FK there.
    #
    # index=False on the tables whose reads also sort: those carry a composite
    # (tenant_id, <sort key>) in __table_args__ instead, which serves the plain
    # tenant_id lookup too. A second single-column index there would be dead
    # weight the import rebuild pays to maintain.
    return mapped_column(GUID, index=index, nullable=False)


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        Index("ix_imports_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_col(index=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    stored_path: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # negociacao|movimentacao|unknown
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Trade(Base):
    # Mirrors taxcore/data/trades.csv (its __source_file column -> source_file here).
    __tablename__ = "trades"
    __table_args__ = (
        # GET /portfolio/trades pages by (date DESC, id DESC).
        Index("ix_trades_tenant_date", "tenant_id", text("date DESC"), text("id DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col(index=False)
    date: Mapped[datetime.date | None] = mapped_column(Date)
    side: Mapped[str | None] = mapped_column(String)
    market: Mapped[str | None] = mapped_column(String)
    maturity: Mapped[str | None] = mapped_column(String)
    broker: Mapped[str | None] = mapped_column(String)
    ticker: Mapped[str | None] = mapped_column(String)
    qty: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    source_file: Mapped[str | None] = mapped_column(String)
    signed_qty: Mapped[float | None] = mapped_column(Float)


class CashEvent(Base):
    # Mirrors taxcore/data/cash_events.csv (__source_file -> source_file).
    __tablename__ = "cash_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    date: Mapped[datetime.date | None] = mapped_column(Date)
    ticker: Mapped[str | None] = mapped_column(String)
    event_type: Mapped[str | None] = mapped_column(String)
    event_category: Mapped[str | None] = mapped_column(String)
    is_amortization: Mapped[bool | None] = mapped_column(Boolean)
    qty: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    broker: Mapped[str | None] = mapped_column(String)
    source_file: Mapped[str | None] = mapped_column(String)


class Position(Base):
    # Mirrors taxcore/data/positions.csv.
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_tenant_ticker", "tenant_id", "ticker"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col(index=False)
    ticker: Mapped[str | None] = mapped_column(String)
    qty: Mapped[float | None] = mapped_column(Float)
    avg_cost: Mapped[float | None] = mapped_column(Float)
    total_cost: Mapped[float | None] = mapped_column(Float)
    market: Mapped[str | None] = mapped_column(String)


class CostBasisOverride(Base):
    # Mirrors taxcore/data/cost_basis_overrides.csv. Empty by default.
    __tablename__ = "cost_basis_overrides"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    date: Mapped[datetime.date | None] = mapped_column(Date)
    ticker: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str | None] = mapped_column(String)  # buy|sell|cost_adjust
    qty: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)


class BacktestRun(Base):
    # One backtest submission per row. The worker shells into the engine venv
    # (scripts/run_backtest_json.py), stores metrics here and artifacts under
    # ARTIFACTS_DIR/{id}/ (curves.csv, metrics.json).
    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("ix_backtest_runs_tenant_created", "tenant_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_col(index=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(  # pending|running|done|error
        String, nullable=False, default="pending"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifacts_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PortfolioSleeve(Base):
    # spec/05 Feature 3: fixed-percentage strategy sleeves. The whole set is
    # replaced atomically per tenant on PUT; percentages sum to 100.
    __tablename__ = "portfolio_sleeves"
    __table_args__ = (
        UniqueConstraint("tenant_id", "strategy_ref", name="uq_sleeves_tenant_strategy"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    strategy_ref: Mapped[str] = mapped_column(Text, nullable=False)  # spec name | 'CASH_CDI'
    target_pct: Mapped[float] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AssetClassOverride(Base):
    # spec/05 Feature 3: mark a held ticker as cash/CDI-equivalent (e.g. LFTB11).
    # Such a ticker's market value counts toward the tenant's cash sleeve instead
    # of showing up as an equity drift/sell. treatment is 'cash_cdi' for now.
    __tablename__ = "asset_class_overrides"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticker", name="uq_asset_override_tenant_ticker"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    treatment: Mapped[str] = mapped_column(Text, nullable=False)  # 'cash_cdi'
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RebalanceSettings(Base):
    # spec/05 Feature 4: per-tenant rebalance behavior. One row per tenant
    # (tenant_id is the PK), upserted on PUT. Defaults reproduce today's behavior.
    __tablename__ = "rebalance_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    buy_only_laggards: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sell_only_for_harvest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    cadence: Mapped[str] = mapped_column(
        Text, nullable=False, default="ME", server_default=text("'ME'")
    )  # ME | QE | W-FRI
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserStrategy(Base):
    # spec/06: a user-authored engine strategy spec (rank_and_hold | fixed_weight),
    # stored verbatim in `spec` (single source of truth). `name` is the registry
    # key BacktestRun.strategy / PortfolioSleeve.strategy_ref store; the row `id`
    # is the on-disk {id}.yaml filename when materialized for the engine subprocess.
    __tablename__ = "user_strategies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_user_strategies_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # rank_and_hold | fixed_weight
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DarfPayment(Base):
    # Mirrors taxcore/data/darf_payments.csv. Empty by default (CRUD lands later).
    __tablename__ = "darf_payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    apuracao: Mapped[str | None] = mapped_column(String)
    codigo: Mapped[str | None] = mapped_column(String)
    classe: Mapped[str | None] = mapped_column(String)
    vendas_brutas: Mapped[float | None] = mapped_column(Float)
    pnl_liquido: Mapped[float | None] = mapped_column(Float)
    prejuizo_compensado: Mapped[float | None] = mapped_column(Float)
    base_calculo: Mapped[float | None] = mapped_column(Float)
    aliquota: Mapped[float | None] = mapped_column(Float)
    valor_principal: Mapped[float | None] = mapped_column(Float)
    vencimento: Mapped[datetime.date | None] = mapped_column(Date)
    data_pagamento: Mapped[datetime.date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String)
    forma_pagamento: Mapped[str | None] = mapped_column(String)
    comprovante: Mapped[str | None] = mapped_column(String)
    relatorio: Mapped[str | None] = mapped_column(String)
    obs: Mapped[str | None] = mapped_column(Text)
