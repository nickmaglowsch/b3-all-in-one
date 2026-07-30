"""Per-tenant admission control on the backtest queue (spec/01, spec/02).

The worker runs two jobs at a time on a VPS it shares with everything else, and
`queueing_lock` only stops one *kind* of job overlapping. Nothing stopped one
paying account from submitting a hundred backtests and owning both slots for the
rest of the day — worker concurrency is a throughput setting, not admission
control, and the comment in worker.py said as much while the queue went
unguarded.

The unit is the account, not the acting book: an empresarial login managing
fifty client books is one customer and one queue's worth of appetite.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD = "quotapassword"


async def _trader(client, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post("/billing/dev-modules", json={"modules": ["bench"]}, headers=headers)
    assert r.status_code == 200, r.text
    return headers


async def _seed_runs(tenant_id: uuid.UUID, statuses: list[str]) -> None:
    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun

    async with async_session_maker() as session:
        for status in statuses:
            session.add(BacktestRun(
                tenant_id=tenant_id, strategy="MomSharpe", status=status, params={}))
        await session.commit()


async def _inflight(user_id: uuid.UUID) -> int:
    from platform_app.db import async_session_maker
    from platform_app.models import User
    from platform_app.routes import inflight_backtests

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        return await inflight_backtests(session, user)


async def _me(client, headers) -> uuid.UUID:
    return uuid.UUID((await client.get("/me", headers=headers)).json()["id"])


async def test_only_unfinished_runs_count_against_the_quota():
    """A quota on work in flight, not on work ever done — otherwise the first
    week's runs paywall the second week's."""
    from platform_app.db import async_session_maker
    from platform_app.models import User

    tid = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(User(
            id=tid, email=f"inflight-{tid}@example.com", hashed_password="x",
            is_active=True, is_verified=True, modules=["bench"], plan_status="active"))
        await session.commit()

    await _seed_runs(tid, ["pending", "running", "done", "error", "done"])
    assert await _inflight(tid) == 2


async def test_a_managed_books_runs_count_against_the_account_that_owns_it(client):
    """Enterprise books are not a way to buy N queues for the price of one."""
    from platform_app.db import async_session_maker
    from platform_app.models import Portfolio

    headers = await _trader(client, "quota-books@example.com")
    account = await _me(client, headers)
    book = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Portfolio(id=book, owner_id=account, label="Cliente A"))
        await session.commit()

    await _seed_runs(account, ["pending"])
    await _seed_runs(book, ["running", "pending"])
    assert await _inflight(account) == 3


async def test_a_strangers_runs_are_not_your_problem(client):
    headers = await _trader(client, "quota-mine@example.com")
    mine = await _me(client, headers)
    await _seed_runs(uuid.uuid4(), ["pending"] * 20)
    assert await _inflight(mine) == 0


async def test_submitting_over_the_quota_is_refused_with_the_limit_named(client):
    from platform_app.routes import BACKTEST_QUOTA

    headers = await _trader(client, "quota-full@example.com")
    account = await _me(client, headers)
    await _seed_runs(account, ["pending"] * BACKTEST_QUOTA["bench"])

    r = await client.post("/backtests", headers=headers, json={"strategy": "MomSharpe"})
    assert r.status_code == 429, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "backtest_quota"
    assert detail["limit"] == BACKTEST_QUOTA["bench"]
    # Refused before the gallery subprocess: a queue that is already full has no
    # reason to spend a python interpreter finding out whether the name is real.
    assert detail["running"] >= detail["limit"]


async def test_the_bundle_buys_a_deeper_queue(client):
    from platform_app.routes import BACKTEST_QUOTA

    headers = await _trader(client, "quota-upgrade@example.com")
    account = await _me(client, headers)
    await _seed_runs(account, ["pending"] * BACKTEST_QUOTA["bench"])
    assert (await client.post(
        "/backtests", headers=headers, json={"strategy": "MomSharpe"})
    ).status_code == 429

    r = await client.post("/billing/dev-modules", json={"modules": ["empresarial"]}, headers=headers)
    assert r.status_code == 200, r.text
    # Same runs in flight, more room for them — so the refusal is the quota and
    # not something else about this account.
    r = await client.post("/backtests", headers=headers, json={"strategy": "nope"})
    assert r.status_code != 429, r.text
