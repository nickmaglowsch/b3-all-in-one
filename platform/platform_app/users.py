import time
import uuid

import jwt
from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.authentication.transport import (
    Transport,
    TransportLogoutNotSupportedError,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.schemas import BaseUser, BaseUserCreate
from httpx_oauth.clients.google import GoogleOAuth2
from starlette.responses import RedirectResponse, Response

from platform_app.config import settings
from platform_app.db import get_user_db
from platform_app.models import User


class UserRead(BaseUser[uuid.UUID]):
    pass


class UserCreate(BaseUserCreate):
    pass


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")

# ── Session lifetime ──────────────────────────────────────────────────────────
# The token is a bearer credential the SPA keeps in localStorage and nothing can
# revoke, so it was minted for one hour. With no renewal anywhere, that hour was
# the *whole session*: an open tab stopped working at the wall and the next click
# landed on /login, which is what "I have to log in again every time you deploy"
# actually was — deploys are just when people go and look.
#
# Splitting the one number in two fixes that without handing out a longer-lived
# credential than the flow needs:
#
#   TOKEN_TTL_S       how long one token is accepted. Still bounded, so a token
#                     that leaks out of localStorage is not a permanent key.
#   SESSION_MAX_AGE_S how long the session behind it may keep renewing. Every
#                     token carries `sst`, the instant its session began;
#                     POST /auth/refresh re-issues with the *same* `sst` and
#                     refuses once it is older than this. Sliding without a cap
#                     is what would make a stolen token immortal, so the cap is
#                     the point, not a detail.
#
# The audience is pinned here rather than left to the JWTStrategy default because
# refresh decodes the presented token itself and has to ask for the same one.
TOKEN_AUDIENCE = ["fastapi-users:auth"]
TOKEN_TTL_S = 12 * 3600
SESSION_MAX_AGE_S = 30 * 24 * 3600
SESSION_START_CLAIM = "sst"


class SessionJWTStrategy(JWTStrategy):
    """JWTStrategy that stamps when the session started.

    Identical to upstream except for the extra claim — `read_token` ignores
    unknown claims, so tokens minted before this shipped keep working.
    """

    async def write_token(self, user: User, session_start: int | None = None) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            SESSION_START_CLAIM: int(time.time()) if session_start is None else session_start,
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )


def get_jwt_strategy() -> SessionJWTStrategy:
    return SessionJWTStrategy(
        secret=settings.secret_key,
        lifetime_seconds=TOKEN_TTL_S,
        token_audience=TOKEN_AUDIENCE,
    )


def session_started_at(token: str) -> int | None:
    """`sst` out of a token the caller has *already* authenticated.

    None when the token carries no stamp — every token issued before this shipped
    is in that class, and the deploy that ships it must not log those sessions
    out. The caller reads None as "this session starts now", which costs at most
    one extra renewal window and nothing else.
    """
    try:
        data = decode_jwt(token, settings.secret_key, TOKEN_AUDIENCE)
    except jwt.PyJWTError:
        return None
    stamp = data.get(SESSION_START_CLAIM)
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return None
    return int(stamp)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


class RedirectTransport(Transport):
    """OAuth-only transport: the Google callback ends in a browser redirect, so
    send the user back to the SPA's /auth/callback carrying the JWT (the static
    frontend has no server to read a JSON body). The SPA stores it and drops it
    from the URL.

    In the **fragment**, not the query string. A query string is logged verbatim
    by Cloudflare Pages and every proxy in between, and this token is a full
    bearer credential that JWTStrategy cannot revoke — so a line in an access
    log is a working session for whoever reads it. Fragments are never sent to a
    server at all. Keep it that way.
    """

    scheme = None  # type: ignore[assignment]

    def __init__(self, redirect_url: str):
        self.redirect_url = redirect_url

    async def get_login_response(self, token: str) -> Response:
        return RedirectResponse(f"{self.redirect_url}#token={token}", status_code=302)

    async def get_logout_response(self) -> Response:
        raise TransportLogoutNotSupportedError()

    @staticmethod
    def get_openapi_login_responses_success() -> dict:
        return {}

    @staticmethod
    def get_openapi_logout_responses_success() -> dict:
        return {}


# Callback lands on the frontend (FRONTEND_URL), NOT the API.
oauth_backend = AuthenticationBackend(
    name="jwt-redirect",
    transport=RedirectTransport(f"{settings.frontend_url}/auth/callback"),
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_optional_user = fastapi_users.current_user(active=True, optional=True)


def get_google_oauth_client() -> GoogleOAuth2:
    # Lazy: constructed only when the /auth/google router is mounted, from settings.
    return GoogleOAuth2(settings.google_oauth_client_id, settings.google_oauth_client_secret)
