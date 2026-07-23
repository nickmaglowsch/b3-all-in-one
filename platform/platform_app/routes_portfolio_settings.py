"""Per-tenant portfolio settings: asset-class overrides (e.g. mark LFTB11 as a
cash/CDI-equivalent) and rebalance behavior (buy-only-laggards, harvest-only-sells,
cadence). Consumed by the target/rebalance logic in routes.py.

Owned by the settings agent. Registered in main.py as `portfolio_settings_router`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.db import get_async_session
from platform_app.models import AssetClassOverride, RebalanceSettings
from platform_app.tenancy import get_current_tenant

portfolio_settings_router = APIRouter(prefix="/portfolio/settings", tags=["portfolio-settings"])

# Only cash/CDI-equivalence is modelled today (spec/05 Feature 3).
VALID_TREATMENTS = {"cash_cdi"}
# ME = mensal, QE = trimestral, W-FRI = semanal (sexta) — the engine's freq codes.
VALID_CADENCES = {"ME", "QE", "W-FRI"}


# ── Asset-class overrides ─────────────────────────────────────────────────────

class AssetOverrideIn(BaseModel):
    ticker: str
    treatment: str


@portfolio_settings_router.get("/asset-overrides")
async def get_asset_overrides(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (await session.scalars(
        select(AssetClassOverride).where(AssetClassOverride.tenant_id == tid)
        .order_by(AssetClassOverride.ticker)
    )).all()
    return [{"ticker": r.ticker, "treatment": r.treatment} for r in rows]


@portfolio_settings_router.put("/asset-overrides")
async def put_asset_overrides(
    body: list[AssetOverrideIn],
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """Replace the whole override set atomically. Tickers are upper-cased; a
    ticker may appear once (unique tenant+ticker)."""
    _user, tid = tenant
    seen: dict[str, str] = {}
    for o in body:
        treat = o.treatment.strip().lower()
        if treat not in VALID_TREATMENTS:
            raise HTTPException(422, f"unknown treatment: {o.treatment}")
        tk = o.ticker.strip().upper()
        if not tk:
            raise HTTPException(422, "empty ticker")
        if tk in seen:
            raise HTTPException(422, f"duplicate ticker: {tk}")
        seen[tk] = treat

    await session.execute(
        delete(AssetClassOverride).where(AssetClassOverride.tenant_id == tid))
    for tk, treat in seen.items():
        session.add(AssetClassOverride(tenant_id=tid, ticker=tk, treatment=treat))
    await session.commit()
    return [{"ticker": tk, "treatment": treat} for tk, treat in seen.items()]


# ── Rebalance behavior ────────────────────────────────────────────────────────

class RebalanceSettingsIn(BaseModel):
    buy_only_laggards: bool = False
    sell_only_for_harvest: bool = False
    cadence: str = "ME"


def _settings_dict(row: RebalanceSettings | None) -> dict:
    if row is None:  # defaults reproduce today's rebalance behavior exactly
        return {"buy_only_laggards": False, "sell_only_for_harvest": False, "cadence": "ME"}
    return {
        "buy_only_laggards": bool(row.buy_only_laggards),
        "sell_only_for_harvest": bool(row.sell_only_for_harvest),
        "cadence": row.cadence,
    }


@portfolio_settings_router.get("/rebalance")
async def get_rebalance_settings(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return _settings_dict(await session.get(RebalanceSettings, tid))


@portfolio_settings_router.put("/rebalance")
async def put_rebalance_settings(
    body: RebalanceSettingsIn,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    if body.cadence not in VALID_CADENCES:
        raise HTTPException(422, f"cadence must be one of {sorted(VALID_CADENCES)}")
    row = await session.get(RebalanceSettings, tid)
    if row is None:
        row = RebalanceSettings(tenant_id=tid)
        session.add(row)
    row.buy_only_laggards = body.buy_only_laggards
    row.sell_only_for_harvest = body.sell_only_for_harvest
    row.cadence = body.cadence
    await session.commit()
    return _settings_dict(row)
