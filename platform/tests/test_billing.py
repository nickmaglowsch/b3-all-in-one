"""Paywall + webhook. Uses a dedicated user (the dev-login tenant is seeded on the
top tier so the rest of the suite isn't paywalled), and signs webhook payloads by
hand so the signature check is exercised for real — no Stripe network calls."""

import hashlib
import hmac
import json
import time

import pytest

from platform_app.billing import LADDER
from platform_app.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

EMAIL = "billing@example.com"
PASSWORD = "billingpassword"
WEBHOOK_SECRET = "whsec_test_secret"
PRICE_INVESTIDOR = "price_test_investidor"
PRICE_TRADER = "price_test_trader"


@pytest.fixture(scope="module", autouse=True)
def _stripe_config():
    # billing.py reads settings at call time, so plain assignment is enough.
    settings.stripe_webhook_secret = WEBHOOK_SECRET
    settings.stripe_price_investidor = PRICE_INVESTIDOR
    settings.stripe_price_trader = PRICE_TRADER


async def _auth(client) -> dict[str, str]:
    await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": EMAIL, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _set_plan(client, headers, plan: str) -> None:
    r = await client.post("/billing/dev-plan", json={"plan": plan}, headers=headers)
    assert r.status_code == 200, r.text


def _signed(payload: dict) -> tuple[str, dict[str, str]]:
    body = json.dumps(payload)
    ts = int(time.time())
    sig = hmac.new(
        WEBHOOK_SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def _sub_event(kind: str, *, price: str, created: int, status="active", **ident) -> dict:
    return {
        "id": f"evt_{created}",
        "object": "event",  # the SDK reads this to tell v1 events from v2
        "type": f"customer.subscription.{kind}",
        "created": created,
        "data": {
            "object": {
                "object": "subscription",
                "status": status,
                "items": {"data": [{"price": {"id": price}, "current_period_end": created + 86400}]},
                **ident,
            }
        },
    }


async def test_free_tenant_is_paywalled(client):
    headers = await _auth(client)

    # Free tier still gets the book it imported.
    assert (await client.get("/portfolio/positions", headers=headers)).status_code == 200

    r = await client.post("/irpf/reports", json={"year": 2025}, headers=headers)
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail == {
        "error": "plan_required",
        "required_plan": "investidor",
        "current_plan": "gratis",
    }

    assert (await client.get("/backtests", headers=headers)).status_code == 402


async def test_plan_ladder_unlocks_by_tier(client):
    headers = await _auth(client)

    await _set_plan(client, headers, "investidor")
    # Gated on investidor: reachable now (500 would mean taxcore blew up, not a paywall).
    assert (await client.get("/portfolio/sleeves", headers=headers)).status_code == 200
    # Trader-only, still shut.
    assert (await client.get("/backtests", headers=headers)).status_code == 402
    assert (await client.get("/strategies/user", headers=headers)).status_code == 402

    await _set_plan(client, headers, "trader")
    assert (await client.get("/backtests", headers=headers)).status_code == 200
    assert (await client.get("/strategies/user", headers=headers)).status_code == 200

    await _set_plan(client, headers, "gratis")
    assert (await client.get("/backtests", headers=headers)).status_code == 402


async def test_webhook_rejects_bad_signature(client):
    body, _ = _signed(_sub_event("created", price=PRICE_TRADER, created=int(time.time())))
    r = await client.post(
        "/billing/webhook",
        content=body,
        headers={"stripe-signature": "t=1,v1=deadbeef", "content-type": "application/json"},
    )
    assert r.status_code == 400


async def test_webhook_drives_entitlement(client):
    headers = await _auth(client)
    await _set_plan(client, headers, "gratis")
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]

    # 1. First event identifies the tenant by metadata and stores the customer id.
    now = int(time.time())
    body, sig = _signed(
        _sub_event(
            "created",
            price=PRICE_INVESTIDOR,
            created=now,
            customer="cus_billing_test",
            metadata={"tenant_id": tenant_id},
        )
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200

    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["plan"] == "investidor"
    assert sub["status"] == "active"
    assert sub["manageable"] is True
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code != 402

    # 2. Upgrade, resolved by customer id alone (no metadata).
    body, sig = _signed(
        _sub_event("updated", price=PRICE_TRADER, created=now + 10, customer="cus_billing_test")
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["plan"] == "trader"

    # 3. Cancellation drops to free.
    body, sig = _signed(
        _sub_event(
            "deleted", price=PRICE_TRADER, created=now + 20,
            status="canceled", customer="cus_billing_test",
        )
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["plan"] == "gratis"
    assert (await client.get("/backtests", headers=headers)).status_code == 402

    # 4. A late-arriving older event must not resurrect the dead plan.
    body, sig = _signed(
        _sub_event("updated", price=PRICE_TRADER, created=now + 15, customer="cus_billing_test")
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["plan"] == "gratis"


async def test_unknown_price_grants_nothing(client):
    headers = await _auth(client)
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    body, sig = _signed(
        _sub_event(
            "created", price="price_someone_elses_product", created=int(time.time()) + 100,
            customer="cus_billing_test", metadata={"tenant_id": tenant_id},
        )
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["plan"] == LADDER[0]


async def test_checkout_requires_configured_price(client):
    headers = await _auth(client)
    r = await client.post("/billing/checkout", json={"plan": "gratis"}, headers=headers)
    assert r.status_code == 400
