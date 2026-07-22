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
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_app.db import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    # default __tablename__ = "oauth_account"
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    # default __tablename__ = "user" (reserved word, but SQLAlchemy auto-quotes it)
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(lazy="joined")


# --- Tenant data plane (Wave 2). Every table carries tenant_id (== user.id). ---
# The trades/cash_events/positions rows are a deterministic full rebuild by
# taxcore's load_b3; the import job replaces them wholesale per tenant per run.


def _tenant_col() -> Mapped[uuid.UUID]:
    return mapped_column(GUID, ForeignKey("user.id"), index=True, nullable=False)


class Import(Base):
    __tablename__ = "imports"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_col()
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
