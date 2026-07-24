"""Stripe billing: the plan ladder, the entitlement dependency, and /billing routes.

Tiers mirror the landing page — gratis < investidor (R$29/mês) < trader (R$79/mês).
Entitlements live on the user row (== tenant row, spec/01) and are written *only* by
the webhook: checkout returns a URL, it never grants access. That way a user who
abandons Stripe's hosted page, or whose card later fails, is never entitled by us.
"""

import datetime
import json
import uuid

import stripe
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.config import settings
from platform_app.db import get_async_session
from platform_app.models import User
from platform_app.tenancy import get_current_tenant

billing_router = APIRouter(prefix="/billing", tags=["billing"])

FREE = "gratis"
LADDER = (FREE, "investidor", "trader")
# Stripe keeps a subscription usable while it retries a failed charge (dunning).
# Cutting access on the first decline churns customers over an expired card.
ENTITLED = {"active", "trialing", "past_due"}

stripe.api_key = settings.stripe_secret_key


def price_for(plan: str) -> str:
    return {
        "investidor": settings.stripe_price_investidor,
        "trader": settings.stripe_price_trader,
    }.get(plan, "")


def plan_for_price(price_id: str | None) -> str:
    """Reverse the price→tier map. An unknown price (an old or another product's
    price) is worth no entitlement, so it reads as free."""
    return next((p for p in LADDER[1:] if price_id and price_id == price_for(p)), FREE)


def effective_plan(user: User) -> str:
    """The tier the user can actually use: the paid plan while the subscription is
    in good standing, free otherwise."""
    return user.plan if user.plan_status in ENTITLED else FREE


def require_plan(minimum: str):
    """Dependency factory — 402 unless the tenant's effective plan >= `minimum`.

    Returns the same `(user, tenant_id)` tuple as get_current_tenant, so a gated
    route just swaps its dependency instead of growing a parameter.
    """

    async def dependency(
        tenant: tuple[User, uuid.UUID] = Depends(get_current_tenant),
    ) -> tuple[User, uuid.UUID]:
        current = effective_plan(tenant[0])
        if LADDER.index(current) < LADDER.index(minimum):
            raise HTTPException(
                402,
                detail={
                    "error": "plan_required",
                    "required_plan": minimum,
                    "current_plan": current,
                },
            )
        return tenant

    return dependency


# Module-level singletons: FastAPI caches a dependency per request by function
# identity, so gated routes sharing these resolve get_current_tenant just once.
needs_investidor = require_plan("investidor")
needs_trader = require_plan("trader")


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
async def get_subscription(tenant=Depends(get_current_tenant)) -> dict:
    user, _ = tenant
    return {
        "plan": effective_plan(user),
        "billed_plan": user.plan,
        "status": user.plan_status,
        "renews_at": user.plan_renews_at,
        # No customer yet => nothing for the billing portal to manage.
        "manageable": bool(user.stripe_customer_id),
    }


@billing_router.post("/checkout")
async def create_checkout(
    plan: str = Body(..., embed=True),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Hosted Checkout session for a paid tier. Returns the URL to redirect to."""
    user, _ = tenant
    price = price_for(plan)
    if not price:
        raise HTTPException(400, f"plano sem preço configurado: {plan}")
    _require_stripe()
    checkout = await stripe.checkout.Session.create_async(
        mode="subscription",
        customer=await _ensure_customer(user, session),
        line_items=[{"price": price, "quantity": 1}],
        client_reference_id=str(user.id),
        # Fallback identity for the webhook if the customer id write ever loses a race.
        subscription_data={"metadata": {"tenant_id": str(user.id)}},
        allow_promotion_codes=True,
        success_url=f"{settings.frontend_url}/assinatura?checkout=ok",
        cancel_url=f"{settings.frontend_url}/assinatura?checkout=cancelado",
    )
    return {"url": checkout.url}


@billing_router.post("/portal")
async def create_portal(tenant=Depends(get_current_tenant)) -> dict:
    """Stripe's billing portal — card updates, invoices, cancellation. Stripe owns
    that whole UI, so we own none of it."""
    user, _ = tenant
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

    item = (sub.get("items", {}).get("data") or [{}])[0]
    canceled = event["type"].endswith(".deleted")
    # `current_period_end` sits on the subscription in older API versions and on the
    # item in newer ones (2025-03+); read whichever this account's version sends.
    period_end = sub.get("current_period_end") or item.get("current_period_end")

    user.plan = FREE if canceled else plan_for_price((item.get("price") or {}).get("id"))
    user.plan_status = "canceled" if canceled else sub.get("status")
    user.plan_renews_at = (
        datetime.datetime.fromtimestamp(period_end, datetime.UTC)
        if period_end and not canceled
        else None
    )
    user.plan_event_at = fired_at
    user.stripe_customer_id = user.stripe_customer_id or sub.get("customer")
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

    @billing_router.post("/dev-plan")
    async def dev_set_plan(
        plan: str = Body(..., embed=True),
        tenant=Depends(get_current_tenant),
        session: AsyncSession = Depends(get_async_session),
    ) -> dict:
        """Dev-only: flip the tenant's plan without Stripe, so the paywall is
        testable locally. Never mounted outside development."""
        if plan not in LADDER:
            raise HTTPException(400, f"plano desconhecido: {plan}")
        user, _ = tenant
        user.plan = plan
        user.plan_status = None if plan == FREE else "active"
        session.add(user)
        await session.commit()
        return {"plan": effective_plan(user)}
