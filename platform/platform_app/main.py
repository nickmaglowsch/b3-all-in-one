import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_users import exceptions
from sqlalchemy import text

from platform_app.billing import (
    EMPRESARIAL,
    billing_router,
    needs_bench,
    needs_indice,
    needs_irpf,
    needs_mercado,
)
from platform_app.config import settings
from platform_app.db import engine
from platform_app.models import User
from platform_app.privacy import privacy_router
from platform_app.routes import (
    backtests_router,
    imports_router,
    irpf_router,
    market_router,
    portfolio_router,
    strategies_router,
    tlh_router,
)
from platform_app.routes_backtest_retention import backtest_retention_router
from platform_app.routes_benchmarks import benchmarks_router
from platform_app.routes_portfolio_index import portfolio_index_router
from platform_app.routes_portfolio_settings import portfolio_settings_router
from platform_app.routes_portfolios import portfolios_router
from platform_app.routes_whatif import whatif_router
from platform_app.tenancy import get_current_tenant
from platform_app.users import (
    SESSION_MAX_AGE_S,
    SessionJWTStrategy,
    UserCreate,
    UserRead,
    auth_backend,
    current_active_user,
    fastapi_users,
    get_google_oauth_client,
    get_jwt_strategy,
    get_user_manager,
    oauth_backend,
    session_started_at,
)

app = FastAPI(title="b3-all-in-one platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── OAuth state ↔ browser binding (login CSRF) ────────────────────────────────
# fastapi-users signs the `state` token over an EMPTY payload, so it proves only
# "this server issued a state" — never "*this* browser started the flow". Since
# /auth/google/authorize is unauthenticated, an attacker could run the flow with
# their own Google account, capture the callback URL before fetching it, and hand
# it to a victim: the victim's browser completes a login into the ATTACKER's
# tenant, and every extract they import afterwards lands in the attacker's book.
#
# The fix is a value the attacker cannot plant in the victim's browser. We hash
# the state we just issued into an HttpOnly cookie on /authorize, and on
# /callback require the presented state to hash to that cookie. No change to the
# upstream router or the token payload.
#
# The SPA lives on another *site* (pages.dev → duckdns.org, both on the public
# suffix list), so /authorize is a cross-site fetch: a SameSite=Lax Set-Cookie
# is dropped on arrival and every callback then 400s. None+Secure is what lets
# the cookie be stored at all; it costs nothing here, because the value is a
# digest of a state only the server can mint — an attacker who forces the fetch
# still never learns the state that cookie commits the browser to. Dev is
# same-site on localhost, where Secure would be dropped over plain http.
_OAUTH_STATE_COOKIE = "oauth_state"
_AUTHORIZE_PATH = "/auth/google/authorize"
_CALLBACK_PATH = "/auth/google/callback"
_COOKIE_SITE = (
    {"samesite": "lax", "secure": False} if settings.is_dev
    else {"samesite": "none", "secure": True}
)


def _state_digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


@app.middleware("http")
async def bind_oauth_state(request: Request, call_next):
    if request.url.path == _CALLBACK_PATH:
        presented = request.query_params.get("state", "")
        expected = request.cookies.get(_OAUTH_STATE_COOKIE, "")
        # compare_digest: the comparison is against a value the caller supplies.
        if not expected or not hmac.compare_digest(_state_digest(presented), expected):
            return JSONResponse(
                {"detail": "INVALID_STATE_TOKEN"}, status_code=400)
        response = await call_next(request)
        response.delete_cookie(_OAUTH_STATE_COOKIE, path="/", **_COOKIE_SITE)
        return response

    response = await call_next(request)
    if request.url.path != _AUTHORIZE_PATH or response.status_code != 200:
        return response

    # Buffer the small JSON body so the issued state can be read out of the
    # authorization_url, then hand the identical body onward.
    body = b"".join([chunk async for chunk in response.body_iterator])
    out = Response(
        content=body, status_code=response.status_code,
        headers=dict(response.headers), media_type=response.media_type,
    )
    try:
        url = json.loads(body)["authorization_url"]
        state = parse_qs(urlparse(url).query)["state"][0]
    except (ValueError, KeyError, IndexError):
        return out  # nothing to bind — let the response through unchanged
    out.set_cookie(
        _OAUTH_STATE_COOKIE, _state_digest(state),
        max_age=600, httponly=True, path="/", **_COOKIE_SITE,
    )
    return out

# Google OAuth at /auth/google — the only way in outside development.
#
# associate_by_email welds a Google identity onto whatever row already holds that
# address. That is only safe because the address can no longer be *claimed* by
# anyone: password registration is dev-only (below), so every row here was created
# by a Google sign-in that proved the address. With /auth/register open, anyone
# could POST a stranger's email, wait for them to sign in with Google, and inherit
# their account — permanently, since no password-reset router is mounted either.
app.include_router(
    fastapi_users.get_oauth_router(
        get_google_oauth_client(),
        # Redirect backend: callback bounces the browser to the SPA with the JWT,
        # instead of returning JSON the static frontend couldn't catch.
        oauth_backend,
        settings.secret_key,
        associate_by_email=True,
        is_verified_by_default=True,
    ),
    prefix="/auth/google",
    tags=["auth"],
)

if settings.is_dev:
    # Password login + registration exist for local work and the e2e tests only.
    # The SPA never calls either (app/login/page.tsx offers Google and dev-login),
    # so in production they are pure attack surface: an unauthenticated,
    # unrate-limited credential-stuffing and account-claiming endpoint pair.
    app.include_router(
        fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
        tags=["auth"],
    )

app.include_router(billing_router)
# LGPD Art. 18 rights over the whole account — free for everyone, by law.
app.include_router(privacy_router)

# The free base (`carteira`): import extracts, see the book, read market status.
app.include_router(imports_router)
app.include_router(market_router)
# Listing your books is free (everyone has one); managing clients' is `empresarial`.
app.include_router(portfolios_router)
# Mixed — the paid /portfolio and /strategies routes carry their own gate (routes.py).
app.include_router(portfolio_router)
app.include_router(strategies_router)
# One module per route group, and the boundary is the cost driver, not the
# feature name: TLH, what-if and the rebalance surface all need today's price
# for every held ticker, so they are one module (`mercado`) rather than three.
app.include_router(irpf_router, dependencies=[Depends(needs_irpf)])
app.include_router(tlh_router, dependencies=[Depends(needs_mercado)])
app.include_router(whatif_router, dependencies=[Depends(needs_mercado)])
app.include_router(portfolio_settings_router, dependencies=[Depends(needs_mercado)])
app.include_router(portfolio_index_router, dependencies=[Depends(needs_indice)])
app.include_router(backtests_router, dependencies=[Depends(needs_bench)])
# Deleting runs: same resource and same gate as the router above, split off only
# because routes.py is at its size ceiling (scripts/quality_gates.py).
app.include_router(backtest_retention_router, dependencies=[Depends(needs_bench)])
# The rulers the bench draws a run against. Gated with the bench itself: they
# are only reachable from it, and the engine subprocess behind them is not free.
app.include_router(benchmarks_router, dependencies=[Depends(needs_bench)])


@app.get("/health")
async def health() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/auth/refresh", tags=["auth"])
async def refresh_session(
    request: Request,
    user: User = Depends(current_active_user),
    strategy: SessionJWTStrategy = Depends(get_jwt_strategy),
) -> dict[str, str]:
    """Mint the next token of the session the caller is already in.

    A renewal, not a login: the `sst` stamp rides across, so the chain of
    renewals expires SESSION_MAX_AGE_S after the *first* sign-in no matter how
    many times it slid. The SPA calls this on load and while a tab is in use,
    which is what keeps a session that is being used from ending at the token's
    TTL — the failure everyone read as "the deploy logged me out".

    Nothing here touches the browser's selected book: renewing is not switching
    accounts, and clearing it would drop an advisor out of the client's book they
    were reading.
    """
    presented = request.headers.get("Authorization", "")
    raw = presented[7:] if presented[:7].lower() == "bearer " else ""
    # None => a token minted before `sst` existed. Reading that as "starts now"
    # is deliberate: the alternative is logging every open session out on the
    # deploy that adds the claim, which is the bug this endpoint exists to fix.
    started = session_started_at(raw)
    now = int(time.time())
    if started is None:
        started = now
    if now - started > SESSION_MAX_AGE_S:
        raise HTTPException(401, "Sessão expirada — entre novamente.")
    return {
        "access_token": await strategy.write_token(user, session_start=started),
        "token_type": "bearer",
    }


@app.get("/me", tags=["users"])
async def me(tenant: tuple = Depends(get_current_tenant)) -> dict[str, str]:
    user, tenant_id = tenant
    return {"id": str(user.id), "email": user.email, "tenant_id": str(tenant_id)}


if settings.is_dev:
    # Dev-only shortcut: no Google creds needed for local work. Returns a JWT for a
    # seeded dev user (created on first call). Never mounted outside development.
    _DEV_EMAIL = "dev@example.com"

    @app.post("/auth/dev-login", tags=["auth"])
    async def dev_login(
        user_manager=Depends(get_user_manager),
        strategy=Depends(get_jwt_strategy),
    ) -> dict[str, str]:
        try:
            user = await user_manager.get_by_email(_DEV_EMAIL)
        except exceptions.UserNotExists:
            user = await user_manager.create(
                UserCreate(email=_DEV_EMAIL, password="devpassword", is_verified=True)
            )
            # The dev tenant starts on the bundle so seeding and e2e aren't
            # paywalled; POST /billing/dev-modules narrows it to exercise the
            # paywall. One id, because `empresarial` implies every module.
            user = await user_manager.user_db.update(
                user, {"modules": [EMPRESARIAL], "plan_status": "active"}
            )
        token = await strategy.write_token(user)
        return {"access_token": token, "token_type": "bearer"}
