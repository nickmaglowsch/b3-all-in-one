import uuid

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
from httpx_oauth.clients.google import GoogleOAuth2
from starlette.responses import RedirectResponse, Response

from platform_app.config import settings
from platform_app.db import get_user_db
from platform_app.models import User


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.secret_key, lifetime_seconds=3600)


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
    1h bearer credential that JWTStrategy cannot revoke — so a line in an access
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
