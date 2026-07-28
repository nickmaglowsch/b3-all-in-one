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


class RendaFixaTerms(Base):
    """What a still-open renda fixa contract pays, for the tenant to fill in.

    A redeemed contract needs no row: B3 reported both its aplicação and its
    resgate, and two marks imply the rate exactly. One still held has only its
    aplicação, and neither extract B3 publishes carries the indexer — so the
    only honest source for it is the person who signed the paper. Absent a row,
    the contract stays off the return chart and is disclosed by name.

    `rate_pct` reads against `indexer`: the percentage for cdi_pct (95 = 95% do
    CDI), the spread for cdi_plus / ipca_plus (2.5 = CDI + 2,5% a.a.), the
    coupon for pre (12 = 12% a.a. prefixado).
    """

    __tablename__ = "renda_fixa_terms"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticker", name="uq_rf_terms_tenant_ticker"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    indexer: Mapped[str] = mapped_column(Text, nullable=False)  # cdi_pct|cdi_plus|ipca_plus|pre
    rate_pct: Mapped[float] = mapped_column(Numeric, nullable=False)
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


class IngestRun(Base):
    """One market-data ingestion attempt (prices or CVM fundamentals).

    Not tenant-scoped: the market plane is shared infrastructure, not anyone's
    book — which is also why it is deliberately absent from the LGPD export and
    erasure sweeps (they key on tenant_id).

    Exists so a failed refresh is visible. The price plane sat seven months stale
    with nothing anywhere saying so; the pipeline logs lived and died inside a
    container. A row per run, surfaced by GET /market/status, is what turns that
    into something noticeable.
    """

    __tablename__ = "ingest_runs"
    __table_args__ = (
        # The only read is "latest run per kind" (GET /market/status).
        Index("ix_ingest_runs_kind_started", "kind", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # prices | fundamentals
    status: Mapped[str] = mapped_column(String, nullable=False)  # running|done|error
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Tail of the subprocess output on failure. Never returned to tenants — it
    # carries server paths (see the same reasoning in privacy._INTERNAL_COLUMNS).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaxSummary(Base):
    """Cached `tlh_plan --json` with no sells — the book's current tax situation.

    Cached because producing it dumps the tenant's whole trade history to CSV and
    shells into taxcore: seconds, not milliseconds. Portfólio is a page-load
    surface, so it reads whatever is here and never waits for the engine.

    One row per tenant. `process_imports` deletes it, because a new extract makes
    the old figure wrong rather than merely old — and a stale tax number is the
    kind of number this product promises never to show without saying so.

    It carries a tenant_id, so privacy._tenant_tables() picks it up for the LGPD
    export and erasure sweeps without anyone editing that file.
    """

    __tablename__ = "tax_summaries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    # The tlh_plan payload verbatim: situation/buckets/rates/isencao_limit.
    # Stored whole rather than shredded into columns — the engine owns this
    # shape, and mirroring it here would mean a migration every time it moves.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
