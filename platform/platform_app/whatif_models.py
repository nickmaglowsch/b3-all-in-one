"""The what-if job table (spec/05 Feature 2), apart from models.py only because
that module is at its size ceiling (scripts/quality_gates.py). models.py imports
it, so it registers on Base.metadata wherever the other tables do."""
import datetime
import uuid

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_app.db import Base
from platform_app.jobstatus import WhatIfStatus, check_sql


class WhatIfRun(Base):
    """One what-if submission: the tenant's real cash flows replayed through up
    to four strategies. Each is a full backtest, so the replay runs on the worker
    (tasks_whatif) and the page polls this row (GET /whatif/{id}).

    `params` holds what the replay needs, fixed at submission — the strategies,
    the cadence and the dated flows — so a later import cannot change a run the
    tenant already asked for. `result` is the response POST /whatif used to give
    synchronously. Bound to one book (tenant_id) like the rest of the data
    plane, so the LGPD sweeps and a client book's offboarding take it along.
    """

    __tablename__ = "whatif_runs"
    __table_args__ = (
        Index("ix_whatif_runs_tenant_created", "tenant_id", text("created_at DESC")),
        CheckConstraint(check_sql(WhatIfStatus), name="ck_whatif_runs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # No FK, same reason as models._tenant_col: a book is a user.id or a portfolios.id.
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=WhatIfStatus.PENDING)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
