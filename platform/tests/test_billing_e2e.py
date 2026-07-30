"""End-to-end da assinatura: do carrinho ao acesso, e do cancelamento de volta.

O único ponto stubado é a fronteira do SDK do Stripe — as três chamadas de rede
que criam customer, sessão de checkout e sessão do portal. Tudo depois disso é o
app de verdade: a rota valida o carrinho, o webhook assinado escreve o
entitlement, e as rotas gated respondem. É exatamente o caminho que quebra em
produção quando alguém muda uma metade sem a outra.

Diferença de test_billing.py: lá o entitlement é escrito pelo atalho de dev, aqui
ninguém escreve à mão. O estado só muda por checkout + webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
import stripe

from platform_app.billing import CARTEIRA, EMPRESARIAL
from platform_app.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

EMAIL = "assinatura-e2e@example.com"
PASSWORD = "e2epassword"
SECRET = "whsec_e2e_secret"


PRICE = {
    "irpf": "price_e2e_irpf",
    "mercado": "price_e2e_mercado",
    "indice": "price_e2e_indice",
    "bench": "price_e2e_bench",
    EMPRESARIAL: "price_e2e_empresarial",
}


class _Stripe:
    """O que o Stripe teria recebido. Guardar a chamada é metade do teste: uma
    linha de checkout com o preço errado é indistinguível de uma correta se só
    olharmos o status 200."""

    def __init__(self, customer: str) -> None:
        self.customer = customer
        self.checkouts: list[dict] = []
        self.portals: list[dict] = []
        self.customers: list[dict] = []


@pytest.fixture
def fake_stripe(request, monkeypatch) -> _Stripe:
    # `stripe_customer_id` é unique na tabela: dois testes com o mesmo id colidem
    # no segundo checkout. O nome do teste é o discriminante mais barato que existe.
    customer = f"cus_{request.node.name[:40]}"
    calls = _Stripe(customer)
    settings.stripe_secret_key = "sk_test_e2e"
    settings.stripe_webhook_secret = SECRET
    for module, price in PRICE.items():
        setattr(settings, f"stripe_price_{module}", price)

    async def customer_create(**kw):
        calls.customers.append(kw)
        return type("C", (), {"id": customer})()

    async def checkout_create(**kw):
        calls.checkouts.append(kw)
        return type("S", (), {"url": "https://checkout.stripe.test/session"})()

    async def portal_create(**kw):
        calls.portals.append(kw)
        return type("P", (), {"url": "https://portal.stripe.test/session"})()

    monkeypatch.setattr(stripe.Customer, "create_async", customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create_async", checkout_create)
    monkeypatch.setattr(stripe.billing_portal.Session, "create_async", portal_create)
    yield calls
    settings.stripe_secret_key = ""


def _signed(payload: dict) -> tuple[str, dict[str, str]]:
    body = json.dumps(payload)
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return body, {
        "stripe-signature": f"t={ts},v1={sig}",
        "content-type": "application/json",
    }


def _sub(kind: str, modules: list[str], created: int, customer: str, status: str = "active") -> dict:
    return {
        "id": f"evt_{created}",
        "object": "event",
        "type": f"customer.subscription.{kind}",
        "created": created,
        "data": {
            "object": {
                "object": "subscription",
                "status": status,
                "customer": customer,
                "items": {
                    "data": [
                        {"price": {"id": PRICE[m]}, "current_period_end": created + 86400}
                        for m in modules
                    ]
                },
            }
        },
    }


def _prices_of(checkout: dict) -> list[str]:
    return [item["price"] for item in checkout["line_items"]]


async def test_subscription_lifecycle(client, fake_stripe):
    await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    r = await client.post("/auth/jwt/login", data={"username": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]

    # ── 1. Sem assinatura: só a carteira, e o 402 diz qual módulo falta ────────
    sub = (await client.get("/billing", headers=headers)).json()
    assert sub == {
        "modules": [CARTEIRA],
        "billed_modules": [],
        "status": None,
        "renews_at": None,
        "manageable": False,
    }
    r = await client.get("/backtests", headers=headers)
    assert r.status_code == 402
    assert r.json()["detail"]["required_module"] == "bench"

    # Sem customer no Stripe não há nada para gerenciar.
    assert (await client.post("/billing/portal", headers=headers)).status_code == 409

    # ── 2. Checkout de um carrinho de dois módulos ─────────────────────────────
    r = await client.post(
        "/billing/checkout", json={"modules": ["irpf", "bench"]}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://checkout.stripe.test/")

    assert fake_stripe.customers[0]["email"] == EMAIL
    checkout = fake_stripe.checkouts[-1]
    assert sorted(_prices_of(checkout)) == sorted([PRICE["irpf"], PRICE["bench"]])
    assert checkout["mode"] == "subscription"
    assert checkout["customer"] == fake_stripe.customer
    # A identidade tem de viajar nos dois canais: o webhook resolve por customer,
    # e cai no metadata se a escrita do customer perder a corrida.
    assert checkout["client_reference_id"] == tenant_id
    assert checkout["subscription_data"]["metadata"]["tenant_id"] == tenant_id

    # O checkout não dá acesso: quem abandona a página hospedada não fica com nada.
    assert (await client.get("/billing", headers=headers)).json()["billed_modules"] == []
    assert (await client.get("/backtests", headers=headers)).status_code == 402

    # ── 3. O webhook é o que entitula ──────────────────────────────────────────
    now = int(time.time())
    body, sig = _signed(_sub("created", ["irpf", "bench"], now, fake_stripe.customer))
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200

    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["billed_modules"] == ["bench", "irpf"]
    assert sub["modules"] == sorted([CARTEIRA, "bench", "irpf"])
    assert sub["manageable"] is True
    assert sub["renews_at"]

    assert (await client.get("/backtests", headers=headers)).status_code == 200
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code != 402
    # O que não foi comprado continua fechado — não existe escada.
    assert (await client.get("/portfolio/index", headers=headers)).status_code == 402
    assert (await client.post("/portfolios", json={"label": "X"}, headers=headers)).status_code == 402

    # ── 4. Ampliar o carrinho ─────────────────────────────────────────────────
    r = await client.post(
        "/billing/checkout", json={"modules": ["irpf", "bench", "indice"]}, headers=headers
    )
    assert r.status_code == 200
    assert len(_prices_of(fake_stripe.checkouts[-1])) == 3

    body, sig = _signed(_sub("updated", ["irpf", "bench", "indice"], now + 10, fake_stripe.customer))
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/portfolio/index", headers=headers)).status_code != 402

    # ── 5. Desligar um módulo fecha só aquela porta ────────────────────────────
    body, sig = _signed(_sub("updated", ["irpf", "indice"], now + 20, fake_stripe.customer))
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/backtests", headers=headers)).status_code == 402
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code != 402

    # ── 6. Portal do Stripe: existe customer, então é gerenciável ──────────────
    r = await client.post("/billing/portal", headers=headers)
    assert r.status_code == 200, r.text
    assert fake_stripe.portals[-1]["customer"] == fake_stripe.customer

    # ── 7. Cancelamento volta ao ponto de partida ──────────────────────────────
    body, sig = _signed(_sub("deleted", ["irpf", "indice"], now + 30, fake_stripe.customer, status="canceled"))
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["modules"] == [CARTEIRA]
    assert sub["renews_at"] is None
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code == 402
    # A carteira sobrevive ao cancelamento: os dados importados são do tenant.
    assert (await client.get("/portfolio/positions", headers=headers)).status_code == 200


async def test_empresarial_is_bought_alone_and_opens_the_client_books(client, fake_stripe):
    email = "empresarial-e2e@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post("/auth/jwt/login", data={"username": email, "password": PASSWORD})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]

    # Misturar o bundle com um módulo que ele já contém é cobrar duas vezes.
    r = await client.post(
        "/billing/checkout", json={"modules": [EMPRESARIAL, "irpf"]}, headers=headers
    )
    assert r.status_code == 400

    r = await client.post(
        "/billing/checkout", json={"modules": [EMPRESARIAL]}, headers=headers
    )
    assert r.status_code == 200
    assert _prices_of(fake_stripe.checkouts[-1]) == [PRICE[EMPRESARIAL]]

    now = int(time.time()) + 100
    body = json.dumps(
        {
            "id": f"evt_{now}",
            "object": "event",
            "type": "customer.subscription.created",
            "created": now,
            "data": {
                "object": {
                    "object": "subscription",
                    "status": "active",
                    "metadata": {"tenant_id": tenant_id},
                    "items": {
                        "data": [
                            {
                                "price": {"id": PRICE[EMPRESARIAL]},
                                "current_period_end": now + 86400,
                            }
                        ]
                    },
                }
            },
        }
    )
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    r = await client.post(
        "/billing/webhook",
        content=body,
        headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"},
    )
    assert r.status_code == 200

    # Um item comprado, todos os módulos abertos.
    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["billed_modules"] == [EMPRESARIAL]
    assert "bench" in sub["modules"] and "irpf" in sub["modules"]

    r = await client.post("/portfolios", json={"label": "Cliente Um"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    book = r.json()["id"]

    # E o livro do cliente responde às rotas gated com o header de carteira.
    scoped = {**headers, "X-Portfolio-Id": book}
    assert (await client.get("/backtests", headers=scoped)).status_code == 200
