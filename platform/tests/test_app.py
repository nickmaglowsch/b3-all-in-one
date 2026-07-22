import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_dev_login_and_me(client):
    r = await client.post("/auth/dev-login")
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert token

    # unauthenticated /me is rejected
    assert (await client.get("/me")).status_code == 401

    # authed /me works; tenant_id == user id (one user = one tenant)
    r = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "dev@example.com"
    assert body["tenant_id"] == body["id"]
