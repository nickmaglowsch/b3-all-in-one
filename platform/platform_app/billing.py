"""Stripe billing: the module catalogue, the entitlement dependency, /billing routes.

There is no tier ladder. A subscription is a *set* of modules, each one a route
group, each priced by what the server actually spends on it — storing an extract
costs once, running the engine over years of history costs per click. So `bench`
does not imply `irpf`, and no comparison of ranks can express that; entitlement is
set membership.

`empresarial` is the one bundle, and it is not a module you add to a cart: it is
every module, for unlimited client books. Charging it alongside a module it already
contains would bill the same access twice, so the cart refuses the mix.

Entitlements live on the user row (== tenant row, spec/01) and are written *only* by
the webhook: checkout returns a URL, it never grants access. That way a user who
abandons Stripe's hosted page, or whose card later fails, is never entitled by us.

One subscription per tenant is an invariant, not a habit: `mode="subscription"`
always creates a *new* subscription, so a second checkout would bill two of them for
overlapping access, and whichever fired last would rewrite `modules` from its own
items — revoking what the other one is still charging for. So checkout is the first
purchase only; changing the cart later is PUT /billing/modules, which edits the
items of the live subscription.
"""

import datetime
import json
import logging
import uuid

import stripe
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.config import settings
from platform_app.db import get_async_session
from platform_app.models import User
from platform_app.tenancy import get_current_tenant
# Identity-only routes take the user directly: depending on the tenant resolver
# would make billing inherit its 402/404 on a stale book selection, i.e. paywall
# the very page you fix a lapsed subscription on.
from platform_app.users import current_active_user

billing_router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger(__name__)

CARTEIRA = "carteira"
EMPRESARIAL = "empresarial"
# Every id here is a route group that exists and is gated below. Selling a module
# with no code behind it is the failure mode this tuple prevents — "overlays por
# regime" was dropped from the catalogue for exactly that reason: the engine has a
# `regime` field on a strategy spec, not a surface anyone can be denied.
MODULES = (CARTEIRA, "irpf", "mercado", "indice", "bench", EMPRESARIAL)
# What the cart can contain. `carteira` is the free floor, so it is never sold.
SELLABLE = tuple(m for m in MODULES if m != CARTEIRA)
# Stripe keeps a subscription usable while it retries a failed charge (dunning).
# Cutting access on the first decline churns customers over an expired card.
ENTITLED = {"active", "trialing", "past_due"}

stripe.api_key = settings.stripe_secret_key


def price_for(module: str) -> str:
    """The recurring price id for a module. Empty => it cannot be bought, and
    /billing/checkout 400s instead of half-creating a session."""
    return getattr(settings, f"stripe_price_{module}", "") if module in SELLABLE else ""


def module_for_price(price_id: str | None) -> str | None:
    """Reverse the price→module map. An unknown price (an old one, or another
    product on the same Stripe account) is worth no entitlement — None, not the
    free floor, so a cart of several items keeps the ones it does recognise."""
    if not price_id:
        return None
    return next((m for m in SELLABLE if price_id == price_for(m)), None)


def entitled(user: User) -> frozenset[str]:
    """The modules the tenant can use right now.

    `carteira` is unconditional: the extracts they imported are theirs, and a
    lapsed card is not a reason to hide the book. Everything else needs the
    subscription in good standing — the bought set survives a cancellation in the
    record (so /assinatura can show what lapsed) but grants nothing.
    """
    if user.plan_status not in ENTITLED:
        return frozenset({CARTEIRA})
    bought = frozenset(user.modules or ())
    if EMPRESARIAL in bought:
        return frozenset(MODULES)
    return bought | {CARTEIRA}


def require_module(module: str):
    """Dependency factory — 402 unless the tenant is entitled to `module`.

    Returns the same `(user, tenant_id)` tuple as get_current_tenant, so a gated
    route just swaps its dependency instead of growing a parameter.
    """

    async def dependency(
        tenant: tuple[User, uuid.UUID] = Depends(get_current_tenant),
    ) -> tuple[User, uuid.UUID]:
        have = entitled(tenant[0])
        if module not in have:
            raise HTTPException(402, detail=gate_detail(module, have))
        return tenant

    return dependency


def gate_detail(module: str, have: frozenset[str]) -> dict:
    """The 402 body. One shape, so the frontend has one paywall to render — and
    it carries what the tenant *does* have, because "you need bench" is only
    actionable next to "you have irpf"."""
    return {
        "error": "module_required",
        "required_module": module,
        "modules": sorted(have),
    }


# Module-level singletons: FastAPI caches a dependency per request by function
# identity, so gated routes sharing these resolve get_current_tenant just once.
needs_irpf = require_module("irpf")
needs_mercado = require_module("mercado")
needs_indice = require_module("indice")
needs_bench = require_module("bench")
needs_empresarial = require_module(EMPRESARIAL)


def _require_stripe() -> None:
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Stripe não configurado (STRIPE_SECRET_KEY vazio).")


async def _ensure_customer(user: User, session: AsyncSession) -> str:
    """The tenant's Stripe customer, created on first checkout and stored so the
    webhook can resolve events back to this row."""
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = await stripe.Customer.create_async(
        email=user.email, metadata={"tenant_id": str(user.id)}
    )
    user.stripe_customer_id = customer.id
    session.add(user)
    await session.commit()
    return customer.id


@billing_router.get("")
async def get_subscription(user: User = Depends(current_active_user)) -> dict:
    return {
        # What is usable, and what is on the invoice. They differ whenever the
        # subscription lapses, and the difference is the whole message the
        # /assinatura page has to deliver.
        "modules": sorted(entitled(user)),
        "billed_modules": sorted(user.modules or []),
        "status": user.plan_status,
        "renews_at": user.plan_renews_at,
        # No customer yet => nothing for the billing portal to manage.
        "manageable": bool(user.stripe_customer_id),
        # Which door a cart change goes through: PUT /billing/modules on a live
        # subscription, hosted checkout on the first purchase.
        "changeable": _has_live_subscription(user),
    }


def _has_live_subscription(user: User) -> bool:
    return bool(user.stripe_subscription_id) and user.plan_status in ENTITLED


def _validate_cart(modules: list[str]) -> list[str]:
    """Reject a cart before it becomes a Stripe session, because a half-valid
    subscription is a refund conversation. Returns the prices to charge."""
    if not modules:
        raise HTTPException(400, "nenhum módulo selecionado.")
    unknown = sorted(set(modules) - set(SELLABLE))
    if unknown:
        raise HTTPException(400, f"módulo não vendido: {', '.join(unknown)}")
    if EMPRESARIAL in modules and len(set(modules)) > 1:
        # The bundle already contains every module; billing both is billing twice.
        raise HTTPException(
            400, f"o plano {EMPRESARIAL} já inclui todos os módulos — assine só ele."
        )
    prices = []
    for module in dict.fromkeys(modules):  # dedupe, keep the caller's order
        price = price_for(module)
        if not price:
            raise HTTPException(400, f"módulo sem preço configurado: {module}")
        prices.append(price)
    return prices


@billing_router.post("/checkout")
async def create_checkout(
    modules: list[str] = Body(..., embed=True),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Hosted Checkout for the *first* subscription — one line item per module.
    Returns the URL to redirect to; it grants nothing on its own."""
    prices = _validate_cart(modules)
    if _has_live_subscription(user):
        # A second session would create a second subscription: two invoices for
        # overlapping access, and the loser's next event revoking the winner's
        # modules. Changing an existing cart has its own route.
        raise HTTPException(409, "já existe uma assinatura ativa — altere os módulos dela.")
    _require_stripe()
    checkout = await stripe.checkout.Session.create_async(
        mode="subscription",
        customer=await _ensure_customer(user, session),
        line_items=[{"price": p, "quantity": 1} for p in prices],
        client_reference_id=str(user.id),
        # Fallback identity for the webhook if the customer id write ever loses a race.
        subscription_data={"metadata": {"tenant_id": str(user.id)}},
        allow_promotion_codes=True,
        success_url=f"{settings.frontend_url}/assinatura?checkout=ok",
        cancel_url=f"{settings.frontend_url}/assinatura?checkout=cancelado",
    )
    return {"url": checkout.url}


@billing_router.put("/modules")
async def set_modules(
    modules: list[str] = Body(..., embed=True),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Change the cart of a live subscription by editing its items — no second
    subscription, no second card entry, Stripe prorates the difference.

    Writes nothing on the tenant row: the entitlement still arrives as the
    `customer.subscription.updated` webhook, so there is exactly one writer.
    """
    prices = _validate_cart(modules)
    if not _has_live_subscription(user):
        raise HTTPException(409, "nenhuma assinatura ativa para alterar.")
    _require_stripe()
    try:
        sub = await stripe.Subscription.retrieve_async(user.stripe_subscription_id)
    except stripe.InvalidRequestError:
        # O Stripe não conhece mais essa assinatura (apagada no dashboard, id de
        # dev velho). Soltar o id é o caminho de volta: preso, este PUT falha e o
        # checkout responde 409 — as duas portas fechadas. Entitlement fica como
        # está, que continua sendo só do webhook.
        log.warning("assinatura %s não existe no Stripe; soltando o id", user.stripe_subscription_id)
        user.stripe_subscription_id = None
        await session.commit()
        raise HTTPException(409, "a assinatura não existe mais no Stripe — assine de novo.") from None
    # Only the difference: leaving an unchanged item alone keeps its billing anchor,
    # where delete-and-re-add would churn the invoice for no reason.
    current = {item["price"]["id"]: item["id"] for item in sub["items"]["data"]}
    items = [{"id": i, "deleted": True} for price, i in current.items() if price not in prices]
    items += [{"price": p, "quantity": 1} for p in prices if p not in current]
    if not items:
        return {"changed": False}
    await stripe.Subscription.modify_async(
        user.stripe_subscription_id, items=items, proration_behavior="create_prorations"
    )
    return {"changed": True}


@billing_router.post("/portal")
async def create_portal(user: User = Depends(current_active_user)) -> dict:
    """Stripe's billing portal — card updates, invoices, cancellation. Stripe owns
    that whole UI, so we own none of it."""
    _require_stripe()
    if not user.stripe_customer_id:
        raise HTTPException(409, "nenhuma assinatura para gerenciar.")
    portal = await stripe.billing_portal.Session.create_async(
        customer=user.stripe_customer_id,
        return_url=f"{settings.frontend_url}/assinatura",
    )
    return {"url": portal.url}


async def _find_user(session: AsyncSession, sub: dict) -> User | None:
    customer_id = sub.get("customer")
    if customer_id:
        user = await session.scalar(
            select(User).where(User.stripe_customer_id == customer_id)
        )
        if user:
            return user
    tenant_id = (sub.get("metadata") or {}).get("tenant_id")
    try:
        return await session.get(User, uuid.UUID(tenant_id)) if tenant_id else None
    except ValueError:
        return None


async def _apply_subscription(event: dict, session: AsyncSession) -> None:
    sub = event["data"]["object"]
    user = await _find_user(session, sub)
    if user is None:
        return  # another product on the same Stripe account — not ours to act on
    fired_at = datetime.datetime.fromtimestamp(event["created"], datetime.UTC)
    if user.plan_event_at and fired_at < user.plan_event_at:
        return  # out-of-order delivery: a stale event must not resurrect a dead plan

    sub_id = sub.get("id")
    live = user.stripe_subscription_id
    if live and sub_id and sub_id != live and user.plan_status in ENTITLED:
        # A second subscription on the same customer (created in the dashboard, or a
        # checkout that escaped the 409). The live one owns the entitlement: letting
        # the intruder write would revoke modules the tenant is still being charged
        # for, and its `deleted` would cancel an access that keeps billing.
        log.warning("evento de %s ignorado: %s é a assinatura viva", sub_id, live)
        return

    items = sub.get("items", {}).get("data") or []
    canceled = event["type"].endswith(".deleted")
    # `current_period_end` sits on the subscription in older API versions and on the
    # item in newer ones (2025-03+); read whichever this account's version sends.
    period_end = sub.get("current_period_end") or (
        items[0].get("current_period_end") if items else None
    )

    # Every item, not just the first: a cart of four modules is four line items,
    # and reading one of them would silently entitle a quarter of what was paid.
    # A new list, never a mutation — JSONB in place is invisible to SQLAlchemy.
    bought = sorted(
        {m for item in items if (m := module_for_price((item.get("price") or {}).get("id")))}
    )
    if canceled:
        user.modules = []
    elif bought:
        user.modules = bought
    else:
        # Not a downgrade: Stripe subscriptions always carry at least one item, so an
        # empty result means no item's price is configured here — a missing
        # STRIPE_PRICE_* in this deploy. Clearing would charge in full and deliver
        # nothing, silently. Keep the set and make noise.
        log.error(
            "assinatura %s: nenhum item bate com um STRIPE_PRICE_* conhecido; "
            "entitlement mantido em %s", sub_id, user.modules
        )
    user.plan_status = "canceled" if canceled else sub.get("status")
    user.plan_renews_at = (
        datetime.datetime.fromtimestamp(period_end, datetime.UTC)
        if period_end and not canceled
        else None
    )
    user.plan_event_at = fired_at
    user.stripe_customer_id = user.stripe_customer_id or sub.get("customer")
    user.stripe_subscription_id = None if canceled else (sub_id or live)
    session.add(user)
    await session.commit()


@billing_router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request, session: AsyncSession = Depends(get_async_session)
) -> dict:
    """The only writer of entitlements. Unsigned payloads never reach the DB."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "webhook não configurado (STRIPE_WEBHOOK_SECRET vazio).")
    payload = await request.body()
    try:
        # verify_header + json.loads is exactly what construct_event does, minus the
        # StripeObject wrapper — plain dicts read the same on any SDK version.
        # The tolerance is the replay window; verify_header skips it if not passed.
        stripe.WebhookSignature.verify_header(
            payload.decode(),
            request.headers.get("stripe-signature", ""),
            settings.stripe_webhook_secret,
            tolerance=stripe.Webhook.DEFAULT_TOLERANCE,
        )
        event = json.loads(payload)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(400, f"webhook inválido: {exc}") from exc

    # created/updated/deleted all carry the full subscription, so one handler covers
    # the whole lifecycle — upgrade, downgrade, dunning, cancellation.
    if event["type"].startswith("customer.subscription."):
        await _apply_subscription(event, session)
    return {"received": True}


if settings.is_dev:

    @billing_router.post("/dev-modules")
    async def dev_set_modules(
        modules: list[str] = Body(..., embed=True),
        status: str = Body("active", embed=True),
        user: User = Depends(current_active_user),
        session: AsyncSession = Depends(get_async_session),
    ) -> dict:
        """Dev-only: write the module set without Stripe, so the paywall is
        testable locally. `status` is settable because dunning and cancellation
        are entitlement states, not module states. Never mounted outside dev."""
        unknown = sorted(set(modules) - set(MODULES))
        if unknown:
            raise HTTPException(400, f"módulo desconhecido: {', '.join(unknown)}")
        user.modules = sorted(set(modules) - {CARTEIRA})
        user.plan_status = status if user.modules else None
        session.add(user)
        await session.commit()
        return {"modules": sorted(entitled(user))}
