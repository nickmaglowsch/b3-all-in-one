"""GET /market/assets — find a listed asset on any market plane.

What the /estrategia sleeve picker searches as the user types: a ticker prefix
or part of a name, across B3, the US and crypto (or one of them). Each result is
already in the canonical spelling a sleeve stores (asset_refs), with its latest
close in reais, so the picker never offers something the sleeve save would
refuse without saying so first.

Signed-in only: unlike /market/status it is not cached, so it is a query per
keystroke against the market plane, and nothing anonymous needs it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from platform_app import asset_refs, market_assets
from platform_app.db import get_engine_pool
from platform_app.users import current_active_user

market_assets_router = APIRouter(prefix="/market", tags=["market"])


@market_assets_router.get("/assets")
async def search_assets(
    q: str = Query(min_length=1, max_length=40),
    market: asset_refs.Market | None = None,
    limit: int = Query(20, ge=1, le=market_assets.SEARCH_LIMIT_MAX),
    _user=Depends(current_active_user),
) -> dict:
    """{results: [{ticker, market, name, asset_class, price_brl, price_date}]} —
    exact ticker first, then ticker prefix, then name; delisted names left out.
    `price_brl` is null for an instrument the plane has never priced."""
    if not q.strip():
        raise HTTPException(422, "a busca precisa de pelo menos um caractere")
    markets = [market] if market else list(asset_refs.MARKETS)
    try:
        results = await market_assets.search(await get_engine_pool(), q, markets, limit)
    except Exception as e:  # noqa: BLE001 — an absent or older plane is not a 500
        raise HTTPException(503, "a busca de ativos está indisponível: o plano de "
                                 "preços não pôde ser lido") from e
    return {"results": results}
