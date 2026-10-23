"""The MCP access-token table (spec/09), apart from models.py only because that
module is at its size ceiling (scripts/quality_gates.py). models.py imports it,
so it registers on Base.metadata wherever the other tables do."""
import datetime
import uuid

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_app.db import Base


class McpToken(Base):
    """A revocable access token an AI client presents to POST /mcp.

    Bound to one book (tenant_id), like everything else on the data plane, so the
    LGPD export/erasure sweeps and a client book's offboarding take it along. Only
    the sha256 is stored: the token is shown once, at creation. Unlike the session
    JWT it does not expire — revoking it (DELETE /mcp/tokens/{id}) is how it ends,
    which is the point: an agent left running overnight must not die at a TTL, and
    a leaked one must be killable without logging the person out.
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # No FK, same reason as models._tenant_col: a book is a user.id or a portfolios.id.
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String, nullable=False)  # shown in the list
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
