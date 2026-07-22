from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
)
from sqlalchemy.orm import Mapped, relationship

from platform_app.db import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    # default __tablename__ = "oauth_account"
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    # default __tablename__ = "user" (reserved word, but SQLAlchemy auto-quotes it)
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(lazy="joined")
