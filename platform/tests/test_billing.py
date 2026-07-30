"""Entitlement por módulo + webhook.

Usa um usuário dedicado (o tenant do dev-login é semeado com tudo ligado para o
resto da suíte não bater no paywall) e assina os payloads do webhook à mão, para
a verificação de assinatura ser exercida de verdade — nenhuma chamada de rede ao
Stripe.

O que estes testes travam é a diferença entre este modelo e o anterior: não existe
mais escada. Ligar `bench` não dá `irpf`, e é exatamente essa independência que uma
comparação por índice (o `LADDER.index` de antes) não consegue expressar.
"""

import hashlib
import hmac
import json
import time

import pytest

from platform_app.billing import CARTEIRA, EMPRESARIAL, MODULES
from platform_app.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

EMAIL = "billing@example.com"
PASSWORD = "billingpassword"
WEBHOOK_SECRET = "whsec_test_secret"
PRICES = {
    "irpf": "price_test_irpf",
    "mercado": "price_test_mercado",
    "indice": "price_test_indice",
    "bench": "price_test_bench",
    EMPRESARIAL: "price_test_empresarial",
}


@pytest.fixture(scope="module", autouse=True)
def _stripe_config():
    # billing.py lê settings na hora da chamada, então atribuir basta.
    settings.stripe_webhook_secret = WEBHOOK_SECRET
    for module, price in PRICES.items():
        setattr(settings, f"stripe_price_{module}", price)


async def _auth(client) -> dict[str, str]:
    await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    r = await client.post(
        "/auth/jwt/login", data={"username": EMAIL, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _set_modules(client, headers, *modules: str, status: str = "active") -> None:
    """Atalho de dev: escreve o conjunto sem passar pelo Stripe."""
    r = await client.post(
        "/billing/dev-modules",
        json={"modules": list(modules), "status": status},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return None


def _signed(payload: dict) -> tuple[str, dict[str, str]]:
    body = json.dumps(payload)
    ts = int(time.time())
    sig = hmac.new(
        WEBHOOK_SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def _sub_event(kind: str, *, prices: list[str], created: int, status="active", **ident) -> dict:
    """Uma assinatura com um item por módulo — é assim que o Stripe representa um
    carrinho montado, e é o que o webhook tem de ler por inteiro em vez de olhar
    só o primeiro item."""
    return {
        "id": f"evt_{created}",
        "object": "event",  # o SDK lê isto para distinguir eventos v1 de v2
        "type": f"customer.subscription.{kind}",
        "created": created,
        "data": {
            "object": {
                "object": "subscription",
                "status": status,
                "items": {
                    "data": [
                        {"price": {"id": p}, "current_period_end": created + 86400}
                        for p in prices
                    ]
                },
                **ident,
            }
        },
    }


# ── entitlement ───────────────────────────────────────────────────────────────


async def test_carteira_is_free_and_nothing_else_is(client):
    headers = await _auth(client)
    await _set_modules(client, headers)  # nenhum módulo: sem assinatura

    # A carteira que o tenant importou continua dele, de graça.
    assert (await client.get("/portfolio/positions", headers=headers)).status_code == 200

    r = await client.post("/irpf/reports", json={"year": 2025}, headers=headers)
    assert r.status_code == 402
    assert r.json()["detail"] == {
        "error": "module_required",
        "required_module": "irpf",
        "modules": [CARTEIRA],
    }
    assert (await client.get("/backtests", headers=headers)).status_code == 402
    assert (await client.get("/portfolio/sleeves", headers=headers)).status_code == 402


async def test_a_module_opens_only_its_own_routes(client):
    """O ponto do modelo: nada implica nada. Comprar backtests não dá IRPF."""
    headers = await _auth(client)

    await _set_modules(client, headers, "irpf")
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code != 402
    assert (await client.get("/portfolio/sleeves", headers=headers)).status_code == 402
    assert (await client.get("/backtests", headers=headers)).status_code == 402

    # Só o bench: o IRPF fecha de novo. Uma escada nunca permitiria isto.
    await _set_modules(client, headers, "bench")
    assert (await client.get("/backtests", headers=headers)).status_code == 200
    assert (await client.get("/strategies/user", headers=headers)).status_code == 200
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code == 402

    await _set_modules(client, headers, "mercado")
    assert (await client.get("/portfolio/sleeves", headers=headers)).status_code != 402
    assert (await client.get("/backtests", headers=headers)).status_code == 402

    await _set_modules(client, headers, "indice")
    assert (await client.get("/portfolio/index", headers=headers)).status_code != 402


async def test_empresarial_implies_every_module(client):
    """O Empresarial não é um módulo a mais na lista: é todos eles. Vender por
    fora e ainda cobrar cada módulo seria cobrar duas vezes pelo mesmo acesso."""
    headers = await _auth(client)
    await _set_modules(client, headers, EMPRESARIAL)

    entitled = (await client.get("/billing", headers=headers)).json()["modules"]
    assert entitled == sorted(MODULES)
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code != 402
    assert (await client.get("/backtests", headers=headers)).status_code == 200
    r = await client.post("/portfolios", json={"label": "Cliente E2E"}, headers=headers)
    assert r.status_code in (200, 201), r.text


async def test_dunning_keeps_access_but_cancellation_does_not(client):
    headers = await _auth(client)

    # O Stripe mantém a assinatura utilizável enquanto retenta a cobrança.
    await _set_modules(client, headers, "irpf", status="past_due")
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code != 402

    # Cancelada: os módulos comprados continuam no registro, o acesso não.
    await _set_modules(client, headers, "irpf", status="canceled")
    r = await client.get("/billing", headers=headers)
    assert r.json()["modules"] == [CARTEIRA]
    assert r.json()["billed_modules"] == ["irpf"]
    assert (await client.post("/irpf/reports", json={"year": 2025}, headers=headers)).status_code == 402


async def test_billing_separates_what_is_billed_from_what_is_usable(client):
    headers = await _auth(client)
    await _set_modules(client, headers, "irpf", "bench")
    body = (await client.get("/billing", headers=headers)).json()
    assert body["billed_modules"] == ["bench", "irpf"]
    assert body["modules"] == sorted([CARTEIRA, "bench", "irpf"])
    assert body["status"] == "active"


# ── webhook ───────────────────────────────────────────────────────────────────


async def test_webhook_rejects_bad_signature(client):
    body, _ = _signed(_sub_event("created", prices=[PRICES["bench"]], created=int(time.time())))
    r = await client.post(
        "/billing/webhook",
        content=body,
        headers={"stripe-signature": "t=1,v1=deadbeef", "content-type": "application/json"},
    )
    assert r.status_code == 400


async def test_webhook_reads_every_item_not_just_the_first(client):
    headers = await _auth(client)
    await _set_modules(client, headers)
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    now = int(time.time())

    # 1. Carrinho de dois módulos, tenant achado pelo metadata; guarda o customer.
    body, sig = _signed(
        _sub_event(
            "created",
            prices=[PRICES["irpf"], PRICES["bench"]],
            created=now,
            customer="cus_billing_test",
            metadata={"tenant_id": tenant_id},
        )
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["billed_modules"] == ["bench", "irpf"]
    assert sub["status"] == "active"
    assert sub["manageable"] is True
    assert sub["renews_at"]

    # 2. O cliente desliga um módulo: o item sai da assinatura, o acesso sai com ele.
    body, sig = _signed(
        _sub_event("updated", prices=[PRICES["irpf"]], created=now + 10, customer="cus_billing_test")
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["billed_modules"] == ["irpf"]
    assert (await client.get("/backtests", headers=headers)).status_code == 402

    # 3. Cancelamento zera tudo menos a carteira.
    body, sig = _signed(
        _sub_event(
            "deleted", prices=[PRICES["irpf"]], created=now + 20,
            status="canceled", customer="cus_billing_test",
        )
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["modules"] == [CARTEIRA]

    # 4. Evento antigo chegando atrasado não pode ressuscitar o que morreu.
    body, sig = _signed(
        _sub_event("updated", prices=[PRICES["bench"]], created=now + 15, customer="cus_billing_test")
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["modules"] == [CARTEIRA]


async def test_unknown_price_grants_nothing(client):
    """Um preço de outro produto na mesma conta Stripe não vale entitlement — e
    não pode derrubar os módulos que os outros itens do mesmo carrinho trazem."""
    headers = await _auth(client)
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    body, sig = _signed(
        _sub_event(
            "created",
            prices=["price_someone_elses_product", PRICES["irpf"]],
            created=int(time.time()) + 100,
            customer="cus_billing_test",
            metadata={"tenant_id": tenant_id},
        )
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["billed_modules"] == ["irpf"]


# ── checkout ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "modules,reason",
    [
        ([], "carrinho vazio"),
        ([CARTEIRA], "a base não se vende"),
        (["nao_existe"], "módulo desconhecido"),
        ([EMPRESARIAL, "irpf"], "o Empresarial já contém o irpf"),
    ],
)
async def test_checkout_refuses_a_cart_that_cannot_be_charged(client, modules, reason):
    headers = await _auth(client)
    r = await client.post("/billing/checkout", json={"modules": modules}, headers=headers)
    assert r.status_code == 400, f"{reason}: {r.text}"


async def test_checkout_refuses_a_module_without_a_configured_price(client):
    headers = await _auth(client)
    settings.stripe_price_indice = ""
    try:
        r = await client.post(
            "/billing/checkout", json={"modules": ["indice"]}, headers=headers
        )
        assert r.status_code == 400
    finally:
        settings.stripe_price_indice = PRICES["indice"]
