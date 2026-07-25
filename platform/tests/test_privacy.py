"""LGPD (Lei 13.709/2018) data-subject rights over the whole account.

Art. 18 gives the titular confirmation of processing (I/II), access and
portability in a machine-readable form (V) and elimination (VI). The three
routes here are those rights, and the tests are written from the titular's
point of view: what do you hold on me, give me all of it, delete all of it.

The account — not the tenant — is the unit: a firm's own book *and* every
client book it manages leave together, which is what "excluir minha conta"
has to mean for the data on disk as much as in Postgres.
"""

import uuid
from pathlib import Path

import pytest

from tests.conftest import seed_tenant_tables

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD = "privacypassword"


async def _auth(client, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": email, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _enterprise(client, email: str) -> dict[str, str]:
    headers = await _auth(client, email)
    r = await client.post(
        "/billing/dev-plan", json={"plan": "enterprise"}, headers=headers
    )
    assert r.status_code == 200, r.text
    return headers


async def _create_book(client, headers, label: str) -> str:
    r = await client.post("/portfolios", json={"label": label}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed(tid: uuid.UUID) -> list:
    from platform_app.db import async_session_maker

    async with async_session_maker() as session:
        return await seed_tenant_tables(session, tid)


async def test_privacy_routes_are_closed_to_anonymous_callers(client):
    for method, path in (
        ("get", "/privacy/summary"),
        ("get", "/privacy/export"),
        ("delete", "/privacy/account"),
    ):
        r = await getattr(client, method)(path)
        assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code}"


async def test_summary_confirms_processing_and_counts_every_book(client):
    """Art. 18 I/II — confirmation that processing exists, and of what."""
    headers = await _enterprise(client, "summary@example.com")
    me_id = (await client.get("/me", headers=headers)).json()["id"]
    client_id = await _create_book(client, headers, "Cliente A")
    await _seed(uuid.UUID(me_id))

    r = await client.get("/privacy/summary", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["account"]["email"] == "summary@example.com"
    assert body["account"]["plan"] == "enterprise"
    assert body["contact"]  # the channel Art. 18 §1 requires

    books = {b["id"]: b for b in body["books"]}
    assert set(books) == {me_id, client_id}
    assert books[me_id]["is_self"] is True
    assert books[client_id]["label"] == "Cliente A"
    # The seeded book reports what it holds; the fresh client book holds nothing.
    assert books[me_id]["records"]["trades"] == 1
    assert books[client_id]["records"]["trades"] == 0


async def test_export_carries_every_row_of_every_book(client):
    """Art. 18 V — access + portability, in a machine-readable format.

    Seeding *every* tenant table means an export that quietly stops covering one
    of them fails here instead of shipping a partial answer to a legal request.
    """
    headers = await _enterprise(client, "export@example.com")
    me_id = (await client.get("/me", headers=headers)).json()["id"]
    client_id = await _create_book(client, headers, "Cliente B")
    tables = await _seed(uuid.UUID(me_id))
    await _seed(uuid.UUID(client_id))
    assert len(tables) >= 11, "tenant tables vanished — is the export still total?"

    r = await client.get("/privacy/export", headers=headers)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.json()

    assert body["generated_at"]
    assert body["account"]["email"] == "export@example.com"
    books = {b["id"]: b for b in body["books"]}
    assert set(books) == {me_id, client_id}
    for book in books.values():
        for table in tables:
            rows = book["tables"][table.name]
            assert len(rows) == 1, f"{table.name} missing from the export"
    # Own rows only, and no server filesystem layout riding along.
    assert books[me_id]["tables"]["imports"][0]["tenant_id"] == me_id
    assert "stored_path" not in books[me_id]["tables"]["imports"][0]


async def test_an_export_never_reaches_another_account(client):
    stranger = await _auth(client, "stranger-privacy@example.com")
    stranger_id = (await client.get("/me", headers=stranger)).json()["id"]
    await _seed(uuid.UUID(stranger_id))

    headers = await _auth(client, "nosy@example.com")
    body = (await client.get("/privacy/export", headers=headers)).json()
    assert [b["id"] for b in body["books"]] != [stranger_id]
    assert all(b["id"] != stranger_id for b in body["books"])
    assert body["books"][0]["tables"]["trades"] == []


async def test_deleting_the_account_erases_every_book_and_the_login(client, tmp_path):
    """Art. 18 VI — elimination. Nothing keyed to the account survives it."""
    from sqlalchemy import func, select

    from platform_app.config import settings
    from platform_app.db import async_session_maker
    from platform_app.models import Portfolio, User

    headers = await _enterprise(client, "erase@example.com")
    me_id = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])
    client_id = uuid.UUID(await _create_book(client, headers, "Cliente C"))
    tables = await _seed(me_id)
    await _seed(client_id)

    # Extracts on disk are personal data too — B3 statements name their holder.
    uploads = Path(settings.uploads_dir)
    for tid in (me_id, client_id):
        (uploads / str(tid)).mkdir(parents=True, exist_ok=True)
        (uploads / str(tid) / "negociacao-2025.xlsx").write_bytes(b"x")

    r = await client.delete("/privacy/account", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "eliminado"

    async with async_session_maker() as session:
        for table in tables:
            for tid in (me_id, client_id):
                left = await session.scalar(
                    select(func.count()).select_from(table).where(table.c.tenant_id == tid)
                )
                assert left == 0, f"{table.name} kept rows after account deletion"
        assert await session.get(Portfolio, client_id) is None
        assert await session.get(User, me_id) is None

    for tid in (me_id, client_id):
        assert not (uploads / str(tid)).exists(), "extracts left on disk"

    # The token outlives the row it names; the account it names must not answer.
    assert (await client.get("/me", headers=headers)).status_code == 401


async def test_deleting_one_account_leaves_the_neighbours_alone(client):
    from sqlalchemy import func, select

    from platform_app.db import async_session_maker
    from platform_app.models import Trade

    neighbour = await _auth(client, "neighbour@example.com")
    neighbour_id = uuid.UUID((await client.get("/me", headers=neighbour)).json()["id"])
    await _seed(neighbour_id)

    leaver = await _auth(client, "leaver@example.com")
    await _seed(uuid.UUID((await client.get("/me", headers=leaver)).json()["id"]))
    assert (await client.delete("/privacy/account", headers=leaver)).status_code == 200

    async with async_session_maker() as session:
        kept = await session.scalar(
            select(func.count()).select_from(Trade).where(Trade.tenant_id == neighbour_id)
        )
    assert kept == 1
    assert (await client.get("/me", headers=neighbour)).status_code == 200
