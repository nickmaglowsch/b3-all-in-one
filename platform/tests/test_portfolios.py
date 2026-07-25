"""Enterprise tier: one login, many client portfolios.

A managed portfolio's id IS a tenant_id, so isolation is asserted through an
ordinary tenant-scoped route (asset overrides) rather than a bespoke fixture —
if the switch works there it works for every route, they all read the same
dependency.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD = "portfoliospassword"
HEADER = "X-Portfolio-Id"


async def _auth(client, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": email, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _set_plan(client, headers, plan: str) -> None:
    r = await client.post("/billing/dev-plan", json={"plan": plan}, headers=headers)
    assert r.status_code == 200, r.text


async def _enterprise(client, email: str) -> dict[str, str]:
    headers = await _auth(client, email)
    await _set_plan(client, headers, "enterprise")
    return headers


async def _create(client, headers, label: str) -> dict:
    r = await client.post("/portfolios", json={"label": label}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _acting(headers: dict[str, str], portfolio_id: str) -> dict[str, str]:
    return {**headers, HEADER: portfolio_id}


async def _put_overrides(client, headers, tickers: list[str]):
    return await client.put(
        "/portfolio/settings/asset-overrides",
        json=[{"ticker": t, "treatment": "cash_cdi"} for t in tickers],
        headers=headers,
    )


async def _get_overrides(client, headers) -> list[str]:
    r = await client.get("/portfolio/settings/asset-overrides", headers=headers)
    assert r.status_code == 200, r.text
    return sorted(o["ticker"] for o in r.json())


async def test_every_account_has_exactly_one_self_portfolio(client):
    headers = await _auth(client, "solo@example.com")
    me = (await client.get("/me", headers=headers)).json()

    r = await client.get("/portfolios", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"id": me["id"], "label": "Minha carteira", "is_self": True}
    ]


async def test_creating_a_client_portfolio_needs_enterprise(client):
    headers = await _auth(client, "notenterprise@example.com")
    await _set_plan(client, headers, "trader")

    r = await client.post("/portfolios", json={"label": "Cliente A"}, headers=headers)
    assert r.status_code == 402
    assert r.json()["detail"] == {
        "error": "plan_required",
        "required_plan": "enterprise",
        "current_plan": "trader",
    }


async def test_enterprise_manages_many_client_portfolios(client):
    headers = await _enterprise(client, "firm@example.com")
    me_id = (await client.get("/me", headers=headers)).json()["id"]

    a = await _create(client, headers, "Cliente A")
    b = await _create(client, headers, "Cliente B")
    assert a["id"] != b["id"] != me_id
    assert a["is_self"] is False

    listed = (await client.get("/portfolios", headers=headers)).json()
    # Own portfolio first, then the managed ones by label.
    assert [p["label"] for p in listed] == ["Minha carteira", "Cliente A", "Cliente B"]
    assert [p["is_self"] for p in listed] == [True, False, False]

    # Same label twice is a double-submit, not a second client.
    r = await client.post("/portfolios", json={"label": "Cliente A"}, headers=headers)
    assert r.status_code == 409


async def test_each_portfolio_has_its_own_data(client):
    headers = await _enterprise(client, "isolation@example.com")
    me_id = (await client.get("/me", headers=headers)).json()["id"]
    client_a = (await _create(client, headers, "Cliente A"))["id"]
    client_b = (await _create(client, headers, "Cliente B"))["id"]

    assert (await _put_overrides(client, headers, ["PETR4"])).status_code == 200
    assert (
        await _put_overrides(client, _acting(headers, client_a), ["LFTB11", "B5P211"])
    ).status_code == 200

    # Each book sees only its own rows; a fresh client starts empty.
    assert await _get_overrides(client, headers) == ["PETR4"]
    assert await _get_overrides(client, _acting(headers, me_id)) == ["PETR4"]
    assert await _get_overrides(client, _acting(headers, client_a)) == ["B5P211", "LFTB11"]
    assert await _get_overrides(client, _acting(headers, client_b)) == []


async def test_a_portfolio_you_do_not_own_does_not_exist(client):
    owner = await _enterprise(client, "owner@example.com")
    theirs = (await _create(client, owner, "Cliente do vizinho"))["id"]

    stranger = await _enterprise(client, "stranger@example.com")
    r = await client.get(
        "/portfolio/settings/asset-overrides", headers=_acting(stranger, theirs)
    )
    assert r.status_code == 404

    # Nor can a stranger rename or delete it.
    assert (
        await client.patch(
            f"/portfolios/{theirs}", json={"label": "hack"}, headers=stranger
        )
    ).status_code == 404
    assert (
        await client.delete(f"/portfolios/{theirs}", headers=stranger)
    ).status_code == 404


async def test_losing_enterprise_locks_the_managed_books(client):
    headers = await _enterprise(client, "downgrade@example.com")
    managed = (await _create(client, headers, "Cliente A"))["id"]
    assert (await _put_overrides(client, _acting(headers, managed), ["PETR4"])).status_code == 200

    await _set_plan(client, headers, "trader")

    # The data survives, but acting on it is what the tier sells.
    r = await client.get(
        "/portfolio/settings/asset-overrides", headers=_acting(headers, managed)
    )
    assert r.status_code == 402
    assert r.json()["detail"]["required_plan"] == "enterprise"
    # Their own book is untouched by the downgrade.
    assert (
        await client.get("/portfolio/settings/asset-overrides", headers=headers)
    ).status_code == 200
    # And it is still listed, so they can see what they'd get back.
    assert len((await client.get("/portfolios", headers=headers)).json()) == 2


async def test_a_malformed_portfolio_header_is_a_bad_request(client):
    headers = await _enterprise(client, "malformed@example.com")
    r = await client.get(
        "/portfolio/settings/asset-overrides", headers=_acting(headers, "not-a-uuid")
    )
    assert r.status_code == 400


async def test_renaming_a_client_portfolio(client):
    headers = await _enterprise(client, "rename@example.com")
    pid = (await _create(client, headers, "Clietne A"))["id"]

    r = await client.patch(f"/portfolios/{pid}", json={"label": "Cliente A"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Cliente A"
    assert [p["label"] for p in (await client.get("/portfolios", headers=headers)).json()] == [
        "Minha carteira",
        "Cliente A",
    ]


async def test_deleting_a_client_portfolio_takes_its_data_with_it(client):
    from sqlalchemy import func, select

    from platform_app.db import async_session_maker
    from platform_app.models import AssetClassOverride

    headers = await _enterprise(client, "offboard@example.com")
    pid = (await _create(client, headers, "Cliente que saiu"))["id"]
    assert (await _put_overrides(client, _acting(headers, pid), ["PETR4"])).status_code == 200

    r = await client.delete(f"/portfolios/{pid}", headers=headers)
    assert r.status_code == 204, r.text

    assert [p["is_self"] for p in (await client.get("/portfolios", headers=headers)).json()] == [True]
    assert (
        await client.get("/portfolio/settings/asset-overrides", headers=_acting(headers, pid))
    ).status_code == 404

    # Offboarding a client means their rows leave too — nothing to orphan.
    async with async_session_maker() as session:
        left = await session.scalar(
            select(func.count())
            .select_from(AssetClassOverride)
            .where(AssetClassOverride.tenant_id == uuid.UUID(pid))
        )
    assert left == 0


async def test_the_own_portfolio_cannot_be_deleted(client):
    headers = await _enterprise(client, "selfdelete@example.com")
    me_id = (await client.get("/me", headers=headers)).json()["id"]

    assert (await client.delete(f"/portfolios/{me_id}", headers=headers)).status_code == 404


async def test_enterprise_sits_on_top_of_the_ladder(client):
    headers = await _enterprise(client, "ladder@example.com")

    # Everything the tiers below unlock, on a client's book as much as their own.
    pid = (await _create(client, headers, "Cliente A"))["id"]
    for h in (headers, _acting(headers, pid)):
        assert (await client.get("/portfolio/sleeves", headers=h)).status_code == 200
        assert (await client.get("/backtests", headers=h)).status_code == 200


async def test_me_reports_the_acting_portfolio(client):
    headers = await _enterprise(client, "acting@example.com")
    pid = (await _create(client, headers, "Cliente A"))["id"]

    me = (await client.get("/me", headers=_acting(headers, pid))).json()
    assert me["tenant_id"] == pid
    assert me["id"] != pid  # identity stays the firm's; only the book changes
