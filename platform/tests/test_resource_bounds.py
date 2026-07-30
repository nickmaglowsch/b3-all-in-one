"""Two request bodies that a tenant could turn into an out-of-memory kill.

Both routes already validated *shape*. Neither bounded *cost*, and on a VPS that
shares a host with Postgres the OOM killer picks the victim, not us.

  · POST /imports capped the compressed upload at 25 MB. An .xlsx is a zip and XML
    compresses ~1000:1, so that cap bought tens of GB of sheet once openpyxl
    materialised it. A real B3 annual extract expands to ~50 KB.

  · POST /whatif took `freq` as a free-form string that reaches pandas .resample()
    over ~20 years of daily prices. "s" asks for a second-resolution frame of
    hundreds of millions of rows — and unlike a backtest this runs inside the
    request, so it is the API container that dies, not the worker.

The freq case is the same hazard BacktestRequest already documents and encodes as
an enum; only the sibling route was missed.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from platform_app.billing import SELLABLE as ALL_MODULES

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD = "correct-horse-battery-staple"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _auth(client, email: str, modules: list[str] | None = None) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.post(
        "/billing/dev-modules",
        json={"modules": modules if modules is not None else list(ALL_MODULES)},
        headers=headers)).status_code == 200
    return headers


def _zipped(uncompressed_bytes: int) -> bytes:
    """A well-formed zip whose single member declares `uncompressed_bytes`.

    Zeroes, so deflate takes it to a few KB on the wire — which is exactly the
    asymmetry the compressed-size cap could not see.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * uncompressed_bytes)
    return buf.getvalue()


async def test_upload_rejects_a_decompression_bomb(client):
    headers = await _auth(client, "bounds-bomb@example.com")
    bomb = _zipped(60 * 1024 * 1024)
    # The point of the test: it sails through the compressed-size cap.
    assert len(bomb) < 1024 * 1024

    r = await client.post(
        "/imports", headers=headers,
        files=[("files", ("negociacao-2024-01.xlsx", bomb, XLSX))])
    assert r.status_code == 413, r.text
    assert "descompactar" in r.json()["detail"]


async def test_upload_accepts_an_ordinary_extract(client):
    """The bound has to be invisible to real files — B3's expand to ~50 KB."""
    headers = await _auth(client, "bounds-ok@example.com")
    r = await client.post(
        "/imports", headers=headers,
        files=[("files", ("negociacao-2024-02.xlsx", _zipped(64 * 1024), XLSX))])
    assert r.status_code == 200, r.text


async def test_upload_still_accepts_a_non_zip(client):
    """Unreadable zips are not rejected here on purpose: openpyxl reads through
    the same zipfile module, so they already fail downstream with a per-import
    error. Rejecting at upload would only move the message — and every other
    test in this suite uploads bytes that are not zips."""
    headers = await _auth(client, "bounds-notzip@example.com")
    r = await client.post(
        "/imports", headers=headers,
        files=[("files", ("negociacao-2024-03.xlsx", b"x" * 10, XLSX))])
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("freq", ["s", "min", "h", "D"])
async def test_whatif_rejects_a_resample_freq_off_the_cadence_enum(client, freq):
    headers = await _auth(client, "bounds-whatif@example.com", modules=["mercado"])
    r = await client.post(
        "/whatif", json={"strategy_ids": ["MomSharpe"], "freq": freq},
        headers=headers)
    # 422 from the model, before any strategy is resolved or the engine is touched.
    assert r.status_code == 422, r.text
