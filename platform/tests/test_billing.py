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
import stripe

from platform_app.billing import CARTEIRA, EMPRESARIAL, MODULES
from platform_app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")

EMAIL = "billing@example.com"
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


async def _auth(client, email: str = EMAIL) -> dict[str, str]:
    """Sem `email`, o usuário compartilhado do arquivo. Com, um tenant só seu — o
    que os testes de webhook precisam: `stripe_subscription_id` é estado que sobra
    de um teste para o outro, e é exatamente o estado que eles medem."""
    return await auth(client, email)


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


def _sub_event(
    kind: str, *, prices: list[str], created: int, status="active", sub_id="sub_billing_test", **ident
) -> dict:
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
                "id": sub_id,
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


async def test_all_prices_unknown_keeps_the_modules_the_tenant_paid_for(client):
    """Um `STRIPE_PRICE_*` errado no deploy faz todo item virar None. Zerar os
    módulos aí seria cobrar em cheio e não entregar nada — sem erro, sem log, sem
    ninguém percebendo. A assinatura ativa mantém o que tinha."""
    headers = await _auth(client, "billing-preco-desconhecido@example.com")
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    now = int(time.time()) + 200

    body, sig = _signed(
        _sub_event("created", prices=[PRICES["bench"]], created=now,
                   customer="cus_unknown_price", metadata={"tenant_id": tenant_id})
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["billed_modules"] == ["bench"]

    body, sig = _signed(
        _sub_event("updated", prices=["price_de_outro_deploy"], created=now + 10,
                   customer="cus_unknown_price", metadata={"tenant_id": tenant_id})
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["billed_modules"] == ["bench"]
    assert (await client.get("/backtests", headers=headers)).status_code == 200


async def test_a_second_subscription_cannot_overwrite_the_live_one(client):
    """O invariante é uma assinatura por tenant. Se uma segunda aparecer mesmo assim
    (criada no dashboard, ou um checkout que escapou), quem manda no entitlement é a
    que está viva — senão o `updated` da intrusa revoga o que a viva paga, e o
    `deleted` dela cancela um acesso que continua sendo cobrado."""
    headers = await _auth(client, "billing-duas-subs@example.com")
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    now = int(time.time()) + 300

    body, sig = _signed(
        _sub_event("created", prices=[PRICES["irpf"], PRICES["bench"]], created=now,
                   sub_id="sub_viva", customer="cus_duas_subs",
                   metadata={"tenant_id": tenant_id})
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200

    for kind, status in (("created", "active"), ("updated", "active"), ("deleted", "canceled")):
        body, sig = _signed(
            _sub_event(kind, prices=[PRICES["indice"]], created=now + 10, status=status,
                       sub_id="sub_intrusa", customer="cus_duas_subs",
                       metadata={"tenant_id": tenant_id})
        )
        assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
        got = (await client.get("/billing", headers=headers)).json()["billed_modules"]
        assert got == ["bench", "irpf"], f"{kind} da intrusa mexeu no entitlement: {got}"


async def test_a_same_second_incomplete_never_overwrites_active(client):
    """`created` do Stripe tem resolução de um segundo, e uma assinatura com SCA
    manda created(incomplete) e updated(active) no mesmo segundo. Entregues fora
    de ordem, o `incomplete` atrasado era aplicado por cima do `active` e o
    cliente cobrado ficava só com a carteira até a renovação. Empate vai para o
    estágio posterior do ciclo de vida; na ordem certa nada muda."""
    headers = await _auth(client, "billing-empate@example.com")
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    now = int(time.time()) + 600

    def _event(kind, status):
        return _signed(_sub_event(
            kind, prices=[PRICES["bench"]], created=now, status=status,
            sub_id="sub_empate", customer="cus_empate", metadata={"tenant_id": tenant_id}))

    for kind, status in (("updated", "active"), ("created", "incomplete")):
        body, sig = _event(kind, status)
        assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["status"] == "active"
    assert "bench" in sub["modules"]

    # Same second, later stage: a cancellation still lands.
    body, sig = _event("deleted", "canceled")
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["modules"] == [CARTEIRA]


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


async def test_checkout_refuses_a_second_subscription(client):
    """`mode="subscription"` sempre cria uma assinatura nova. Um segundo checkout
    cobraria as duas ao mesmo tempo pelo acesso sobreposto, e o `updated` de uma
    revogaria os módulos da outra. Mudar de carrinho é PUT /billing/modules."""
    headers = await _auth(client, "billing-ja-assina@example.com")
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    body, sig = _signed(
        _sub_event("created", prices=[PRICES["irpf"]], created=int(time.time()) + 400,
                   sub_id="sub_ja_existe", customer="cus_ja_assina",
                   metadata={"tenant_id": tenant_id})
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200

    r = await client.post("/billing/checkout", json={"modules": ["irpf", "bench"]}, headers=headers)
    assert r.status_code == 409, r.text


async def test_changing_modules_needs_a_live_subscription(client):
    headers = await _auth(client)
    await _set_modules(client, headers)
    r = await client.put("/billing/modules", json={"modules": ["bench"]}, headers=headers)
    assert r.status_code == 409, r.text


async def test_a_subscription_stripe_no_longer_has_stops_locking_both_doors(client):
    """Apagada no dashboard, ou um id de dev velho: se o PUT falhasse mantendo o
    id, o checkout continuaria respondendo 409 e o cliente não teria porta
    nenhuma. Soltar o id devolve o caminho da compra."""
    headers = await _auth(client, "billing-sub-fantasma@example.com")
    tenant_id = (await client.get("/me", headers=headers)).json()["id"]
    body, sig = _signed(
        _sub_event("created", prices=[PRICES["irpf"]], created=int(time.time()) + 400,
                   sub_id="sub_fantasma", customer="cus_fantasma",
                   metadata={"tenant_id": tenant_id})
    )
    assert (await client.post("/billing/webhook", content=body, headers=sig)).status_code == 200
    assert (await client.get("/billing", headers=headers)).json()["changeable"] is True

    def _raising(code: str):
        async def raise_it(sub_id, **kw):
            raise stripe.InvalidRequestError("No such subscription", "id", code=code)

        return raise_it

    async def _put(code: str):
        key, settings.stripe_secret_key = settings.stripe_secret_key, "sk_test_fake"
        retrieve = stripe.Subscription.retrieve_async
        stripe.Subscription.retrieve_async = _raising(code)
        try:
            return await client.put(
                "/billing/modules", json={"modules": ["bench"]}, headers=headers
            )
        finally:
            stripe.Subscription.retrieve_async = retrieve
            settings.stripe_secret_key = key

    # Qualquer outro 400 do Stripe (chave de teste contra id de produção, por
    # exemplo) é sobre uma assinatura que existe: soltar o id ali liberaria o
    # checkout a criar a segunda, que é o que este arquivo todo evita.
    with pytest.raises(stripe.InvalidRequestError):
        await _put("test_mode_live_mode_mismatch")
    assert (await client.get("/billing", headers=headers)).json()["changeable"] is True

    r = await _put("resource_missing")
    assert r.status_code == 409, r.text
    sub = (await client.get("/billing", headers=headers)).json()
    assert sub["changeable"] is False, "o id ficou preso e o checkout segue trancado"
    assert sub["billed_modules"] == ["irpf"], "soltar o id não é revogar acesso"


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
