"""POST /whatif — replay the tenant's REAL cash flows through an alternative
strategy (spec/05 Feature 2).

Seeds a tiny tenant (a few 2023 trades + positions), then replays them through
MomSharpe (price-only, runs on the 2023-2025 price plane with benchmarks stubbed
via ENGINE_BENCH_OFFLINE=1 from conftest). Run isolated:

    PLATFORM_TEST_DB=platform_test_whatif uv run pytest tests/test_whatif.py
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

STRATEGY = "MomSharpe"


async def _seed_book(tenant_id: uuid.UUID) -> None:
    from platform_app.db import async_session_maker
    from platform_app.models import Position, Trade

    # (date, side, ticker, qty, price, amount, signed_qty)
    trades = [
        ("2023-01-16", "Compra", "PETR4", 1000, 25.0, 25_000.0, 1000),
        ("2023-03-15", "Compra", "VALE3", 500, 70.0, 35_000.0, 500),
        ("2023-06-20", "Compra", "ITUB4", 800, 28.0, 22_400.0, 800),
        ("2023-09-11", "Venda", "PETR4", 300, 30.0, 9_000.0, -300),
    ]
    positions = [
        ("PETR4", 700, 25.0, 17_500.0),
        ("VALE3", 500, 70.0, 35_000.0),
        ("ITUB4", 800, 28.0, 22_400.0),
    ]
    async with async_session_maker() as session:
        for d, side, tk, qty, px, amt, sq in trades:
            session.add(Trade(
                tenant_id=tenant_id, date=date.fromisoformat(d), side=side,
                market="Mercado à Vista", ticker=tk, qty=qty, price=px,
                amount=amt, signed_qty=sq, source_file="seed"))
        for tk, qty, avg, tot in positions:
            session.add(Position(
                tenant_id=tenant_id, ticker=tk, qty=qty, avg_cost=avg,
                total_cost=tot, market="Mercado à Vista"))
        await session.commit()


async def test_whatif_replays_real_flows(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = (await client.get("/me", headers=headers)).json()["id"]
    await _seed_book(uuid.UUID(tid))

    r = await client.post(
        "/whatif", json={"strategy_ids": [STRATEGY], "freq": "ME"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    # Point comparison: same money in (buys − sells − income), real book today.
    # net invested = 25000 + 35000 + 22400 − 9000 = 73400
    assert body["total_invested"] == pytest.approx(73_400.0)
    assert body["actual_value"] > 0        # positions priced off the engine plane
    assert body["as_of"]

    assert len(body["scenarios"]) == 1
    sc = body["scenarios"][0]
    assert sc["strategy"] == STRATEGY
    assert sc["final_value"] > 0
    assert sc["ann_return"] is not None and sc["sharpe"] is not None
    assert sc["max_dd"] <= 0                # drawdown is non-positive

    curve = sc["curve"]
    assert len(curve) > 2
    assert all("date" in p and "aftertax" in p for p in curve)
    assert curve[-1]["aftertax"] == pytest.approx(sc["final_value"])
    assert any(p["aftertax"] > 0 for p in curve)   # the flows actually got deployed


async def test_whatif_unknown_strategy_404(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/whatif", json={"strategy_ids": ["NopeNope"]}, headers=headers)
    assert r.status_code == 404
