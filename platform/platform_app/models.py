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
    # webhook only). `modules` is a set of bought ids: nothing ordered, nothing
    # implied (billing.entitled owns `empresarial`), access still needs plan_status.
    # Reassign it wholesale — in-place JSONB mutation is invisible to SQLAlchemy.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String, unique=True, index=True, nullable=True
    )
    modules: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # The one live subscription (billing.py's invariant): what PUT /billing/modules
    # edits, and what lets the webhook ignore a second one's events.
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
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
    """A book an account manages on someone else's behalf (spec/07, módulo empresarial).

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


class RendaFixaMark(Base):
    """A per-unit price B3 published for a renda fixa contract on a date.

    Read off the tenant's relatório consolidado mensal ("posição"), which is the
    only extract that prices paper still in custody — Negociação and Movimentação
    report fills, so an unredeemed CDB has exactly one price in the whole book
    and one price implies no rate. `Preço Atualizado` is a second dated mark, and
    two marks imply the rate exactly, so a contract with one of these needs no
    RendaFixaTerms row. The report prints an indexer too, but only its family
    (`DI`, `IPCA`, `PREFIXADO`) with neither percentage nor spread, so it cannot
    fill that table and is ignored.

    Derived from the uploaded extracts, so process_imports full-replaces these
    alongside trades and positions — unlike RendaFixaTerms, which the tenant
    typed in and nothing may delete on their behalf.
    """

    __tablename__ = "renda_fixa_marks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticker", "date", name="uq_rf_marks_tenant_ticker_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    price: Mapped[float] = mapped_column(Numeric, nullable=False)


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


class DarfPayment(Base):
    """One DARF the tenant says they paid, for one apuração month and class.

    Only the *payment* lives here. The apuração beside it — gross, resultado,
    compensação, base, alíquota, IR devido — is recomputed from the trade history
    by taxcore's ``irpf_2026 --json`` on every read, exactly like the IRPF report
    (spec/03: "regenerable any time from the trade history — no stored tax state
    to corrupt"). Storing those figures would create a second, staler answer to
    the one question this product exists to get right.

    A table of this name existed once, created by 8083522f305e and dropped unused
    by d5b8e37f1a94. This is the narrower shape the ledger actually needs, and it
    is modelled, so privacy._tenant_tables() reaches it for the LGPD export and
    erasure sweeps — which is the reason the empty one was dropped rather than
    left to sit there uncovered.
    """

    __tablename__ = "darf_payments"
    __table_args__ = (
        # One DARF per month per class: they are separate apurações with separate
        # rates, and paying either one twice is a data-entry mistake, not a case.
        UniqueConstraint("tenant_id", "apuracao", "classe",
                         name="uq_darf_tenant_apuracao_classe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
    # First day of the apuração month. A Date rather than a "YYYY-MM" string so
    # the year filter is a range scan and the ordering is the calendar's.
    apuracao: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    classe: Mapped[str] = mapped_column(Text, nullable=False)  # AÇÃO | FII | BDR_ETF
    valor_principal: Mapped[float] = mapped_column(Numeric, nullable=False)
    # Null while the DARF is issued but not yet paid — the ledger still counts it
    # as recorded, because the alert is about a month nobody has dealt with.
    data_pagamento: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
