"""GET /imports and GET /backtests returned every row the tenant ever created.

Neither had a limit. /backtests carries a metrics JSONB blob per row, so a power
user's listing grew without bound; /imports is smaller but has the same shape.
Both are page-load surfaces.

The subtlety is ordering. /imports used to return oldest-first and the page did
`[...data].reverse()` client-side, so capping the old query would have kept the
oldest rows and dropped the newest — the opposite of what anyone wants. Both
endpoints now order newest-first, which is also what /backtests already did.

The response stays a bare array: the frontend consumes these as ImportRow[] and
BacktestRun[], and an envelope would be a breaking change bought for nothing.
A caller detects truncation the usual way, len(result) == limit.
"""
import pytest

PASSWORD = "correct-horse-battery-staple"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _auth(client, email: str, plan: str = "trader") -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # /backtests is gated on needs_trader.
    assert (await client.post("/billing/dev-plan", json={"plan": plan},
                              headers=headers)).status_code == 200
    return headers


async def _upload(client, headers, name: str) -> None:
    r = await client.post(
        "/imports", headers=headers,
        files=[("files", (name, b"x" * 10, XLSX))])
    assert r.status_code == 200, r.text


@pytest.mark.asyncio(loop_scope="session")
async def test_imports_returns_newest_first(client):
    headers = await _auth(client, "listbounds-imports@example.com")
    names = [f"negociacao-2024-{i:02d}.xlsx" for i in range(1, 6)]
    for n in names:
        await _upload(client, headers, n)

    rows = (await client.get("/imports", headers=headers)).json()
    assert [r["filename"] for r in rows] == list(reversed(names))


@pytest.mark.asyncio(loop_scope="session")
async def test_imports_limit_keeps_the_newest(client):
    headers = await _auth(client, "listbounds-imports-limit@example.com")
    names = [f"negociacao-2023-{i:02d}.xlsx" for i in range(1, 5)]
    for n in names:
        await _upload(client, headers, n)

    rows = (await client.get("/imports?limit=2", headers=headers)).json()
    assert [r["filename"] for r in rows] == list(reversed(names))[:2]


@pytest.mark.asyncio(loop_scope="session")
async def test_imports_offset_pages_backwards_through_history(client):
    headers = await _auth(client, "listbounds-imports-offset@example.com")
    names = [f"negociacao-2022-{i:02d}.xlsx" for i in range(1, 5)]
    for n in names:
        await _upload(client, headers, n)
    page2 = (await client.get("/imports?limit=2&offset=2", headers=headers)).json()
    assert [r["filename"] for r in page2] == list(reversed(names))[2:4]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("path", ["/imports", "/backtests"])
@pytest.mark.parametrize("qs", ["limit=0", "limit=100000", "offset=-1"])
async def test_out_of_range_paging_is_a_422_not_a_500(client, path, qs):
    """`offset=-1` used to reach Postgres as an error and `limit` did not exist;
    a 422 names the bad parameter instead."""
    headers = await _auth(client, "listbounds-bad-params@example.com")
    r = await client.get(f"{path}?{qs}", headers=headers)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio(loop_scope="session")
async def test_backtests_still_newest_first_and_bounded(client):
    headers = await _auth(client, "listbounds-backtests@example.com")
    for _ in range(3):
        r = await client.post("/backtests", headers=headers,
                              json={"strategy": "MomSharpe"})
        assert r.status_code == 200, r.text

    rows = (await client.get("/backtests", headers=headers)).json()
    assert len(rows) == 3
    created = [r["created_at"] for r in rows]
    assert created == sorted(created, reverse=True)

    one = (await client.get("/backtests?limit=1", headers=headers)).json()
    assert len(one) == 1
    assert one[0]["created_at"] == created[0]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("path", ["/imports", "/backtests"])
async def test_default_limit_is_applied(client, path):
    """The whole point: no caller can ask for an unbounded listing by omitting
    parameters, which is what every existing caller does."""
    import inspect

    from platform_app import routes

    fn = routes.list_imports if path == "/imports" else routes.list_backtests
    limit = inspect.signature(fn).parameters["limit"].default
    bounds = {type(m).__name__: getattr(m, a)
              for m in limit.metadata for a in ("ge", "le") if hasattr(m, a)}
    assert limit.default == 200, f"{path} default limit is {limit.default}"
    assert bounds == {"Ge": 1, "Le": 500}, f"{path} bounds are {bounds}"
