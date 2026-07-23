from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_users import exceptions
from sqlalchemy import text

from platform_app.config import settings
from platform_app.db import engine
from platform_app.routes import (
    backtests_router,
    imports_router,
    irpf_router,
    market_router,
    portfolio_router,
    strategies_router,
    tlh_router,
)
from platform_app.routes_portfolio_settings import portfolio_settings_router
from platform_app.routes_whatif import whatif_router
from platform_app.schemas import UserCreate, UserRead
from platform_app.tenancy import get_current_tenant
from platform_app.users import (
    auth_backend,
    fastapi_users,
    get_google_oauth_client,
    get_jwt_strategy,
    get_user_manager,
)

app = FastAPI(title="b3-all-in-one platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routers, imobindex pattern: JWT login at /auth/jwt, Google OAuth at /auth/google.
app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
)
app.include_router(
    fastapi_users.get_oauth_router(
        get_google_oauth_client(),
        auth_backend,
        settings.secret_key,
        associate_by_email=True,
        is_verified_by_default=True,
    ),
    prefix="/auth/google",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"]
)

app.include_router(imports_router)
app.include_router(portfolio_router)
app.include_router(irpf_router)
app.include_router(tlh_router)
app.include_router(strategies_router)
app.include_router(backtests_router)
app.include_router(market_router)
app.include_router(whatif_router)
app.include_router(portfolio_settings_router)


@app.get("/health")
async def health() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


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
        token = await strategy.write_token(user)
        return {"access_token": token, "token_type": "bearer"}
