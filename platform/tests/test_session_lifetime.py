"""The session, as opposed to the token that happens to represent it right now.

A token used to be minted for one hour with nothing anywhere able to renew it,
so an hour *was* the session: a tab that had been open longer than that stopped
working, and the next click landed on /login. Read from the outside that is "the
deploy logged me out" — a deploy is simply when people go back and look.

These pin both halves of the fix: the session slides while it is being used, and
it stops sliding once it is old enough, so a token that leaks out of localStorage
is not a key that renews itself forever.
"""
import time

import pytest
from fastapi_users.jwt import decode_jwt

from platform_app.users import (
    SESSION_MAX_AGE_S,
    SESSION_START_CLAIM,
    TOKEN_AUDIENCE,
    TOKEN_TTL_S,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _claims(token: str) -> dict:
    from platform_app.config import settings

    return decode_jwt(token, settings.secret_key, TOKEN_AUDIENCE)


async def _login(client) -> str:
    return (await client.post("/auth/dev-login")).json()["access_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_a_token_outlives_a_working_afternoon():
    """The regression that started all of this. A one-hour token with no renewal
    path is a session that ends while its owner is still using it — whatever the
    renewal below does, the wall must not be back an hour after sign-in."""
    assert TOKEN_TTL_S >= 8 * 3600


async def test_refresh_hands_back_a_usable_token(client):
    token = await _login(client)
    r = await client.post("/auth/refresh", headers=_bearer(token))
    assert r.status_code == 200

    renewed = r.json()["access_token"]
    me = await client.get("/me", headers=_bearer(renewed))
    assert me.status_code == 200
    assert me.json()["email"] == "dev@example.com"


async def test_refresh_extends_the_expiry_it_replaces(client):
    """Renewal has to move `exp`. A new token with the old expiry would pass every
    other test here and still drop the user at the original wall."""
    token = await _login(client)
    # The two are minted in the same second, so the stamps would tie without this.
    time.sleep(1)
    renewed = (await client.post("/auth/refresh", headers=_bearer(token))).json()[
        "access_token"
    ]
    assert _claims(renewed)["exp"] > _claims(token)["exp"]


async def test_refresh_keeps_the_original_session_start(client):
    """The whole point of `sst`: renewing must not reset the clock the cap below
    is measured against, or the cap can never be reached."""
    token = await _login(client)
    started = _claims(token)[SESSION_START_CLAIM]
    renewed = (await client.post("/auth/refresh", headers=_bearer(token))).json()[
        "access_token"
    ]
    assert _claims(renewed)[SESSION_START_CLAIM] == started


async def test_refresh_is_closed_to_anonymous_callers(client):
    assert (await client.post("/auth/refresh")).status_code == 401


async def test_a_session_stops_sliding_once_it_is_old(client):
    """Sliding without a cap is what would make a stolen token immortal: whoever
    holds it renews it every hour forever. The stamp is inside the signature, so
    the only way to present an old one is to have started the session then."""
    from platform_app.db import async_session_maker, get_user_db
    from platform_app.users import get_jwt_strategy, get_user_manager

    async with async_session_maker() as session:
        user_db = await anext(get_user_db(session))
        manager = await anext(get_user_manager(user_db))
        user = await manager.get_by_email("dev@example.com")

    strategy = get_jwt_strategy()
    stale = await strategy.write_token(
        user, session_start=int(time.time()) - SESSION_MAX_AGE_S - 1
    )

    # Still a valid token — it authenticates anything else.
    assert (await client.get("/me", headers=_bearer(stale))).status_code == 200
    # But its session is spent, so it buys no successor.
    assert (await client.post("/auth/refresh", headers=_bearer(stale))).status_code == 401


async def test_a_token_from_before_the_stamp_is_still_renewable(client):
    """The deploy that adds `sst` must not log out everyone holding a token minted
    without it — that is the exact failure this endpoint exists to remove."""
    from fastapi_users.jwt import generate_jwt

    from platform_app.config import settings
    from platform_app.db import async_session_maker, get_user_db
    from platform_app.users import get_user_manager

    async with async_session_maker() as session:
        user_db = await anext(get_user_db(session))
        manager = await anext(get_user_manager(user_db))
        user = await manager.get_by_email("dev@example.com")

    legacy = generate_jwt(
        {"sub": str(user.id), "aud": TOKEN_AUDIENCE}, settings.secret_key, 3600
    )
    r = await client.post("/auth/refresh", headers=_bearer(legacy))
    assert r.status_code == 200
    # And it comes back stamped, so the cap applies to it from here on.
    assert SESSION_START_CLAIM in _claims(r.json()["access_token"])


async def test_get_jwt_strategy_is_used_by_dev_login(client):
    """dev-login and the Google callback both mint through get_jwt_strategy, so a
    stamp missing here would mean an unstamped session in production too."""
    assert SESSION_START_CLAIM in _claims(await _login(client))
