"""End-to-end backtest bench: dev-login -> strategy gallery -> market status ->
POST a price-only backtest -> run the job inline -> assert sane metrics + curves.

Uses MomSharpe: a price-only rank_and_hold spec that works on the 2023-2025
price plane with no fundamentals. The engine subprocess reads the real `b3`
market DB (ENGINE_DATABASE_URL default) with benchmarks stubbed
(ENGINE_BENCH_OFFLINE=1 from conftest — no yfinance, no BCB).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

STRATEGY = "MomSharpe"


async def test_backtest_bench_e2e(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Gallery (public): the chosen strategy is present, spec-kind, price-only.
    gallery = (await client.get("/strategies")).json()
    by_name = {s["name"]: s for s in gallery}
    assert STRATEGY in by_name, sorted(by_name)
    assert by_name[STRATEGY]["kind"] == "spec"
    assert by_name[STRATEGY]["needs_fundamentals"] is False

    # Market status reports the price plane honestly (0-row tables allowed).
    status = (await client.get("/market/status")).json()
    assert status["prices"]["rows"] > 0
    assert status["prices"]["tickers"] > 0
    assert status["prices"]["min_date"] and status["prices"]["max_date"]
    assert "corporate_actions" in status["tables"]
    assert "fundamentals_pit" in status["tables"]

    # Submit a run over the price window.
    r = await client.post("/backtests", headers=headers, json={
        "strategy": STRATEGY, "start": "2023-01-01", "end": "2025-12-30", "freq": "ME",
    })
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    assert r.json()["status"] == "pending"

    # Run the deferred job inline (the worker never runs in tests).
    from platform_app.tasks import run_backtest

    result = await run_backtest(run_id=run_id)
    assert result["status"] == "done", result

    # Row + metrics.
    row = (await client.get(f"/backtests/{run_id}", headers=headers)).json()
    assert row["status"] == "done"
    m = row["metrics"]
    assert m["strategy"] == STRATEGY
    assert m["periods"] > 5
    for key in ("Sharpe", "Max Drawdown (%)"):
        assert key in m["pretax"] and key in m["aftertax"]
    assert m["aftertax"]["Max Drawdown (%)"] <= 0  # drawdown is non-positive
    assert m["final_aftertax_nav"] > 0
    # After-tax NAV can't exceed pre-tax (tax is a drag, never a subsidy).
    assert m["final_aftertax_nav"] <= m["final_pretax_nav"] + 1e-6

    # Curves endpoint: aligned series incl. the after-tax equity curve.
    curves = (await client.get(f"/backtests/{run_id}/curves", headers=headers)).json()
    s = curves["series"]
    assert len(s["date"]) == m["periods"]
    assert len(s["aftertax"]) == m["periods"]
    assert all(v > 0 for v in s["aftertax"])
    assert set(s) >= {"date", "pretax", "aftertax", "drawdown", "turnover"}

    # Tenant isolation: unknown / other-tenant run id is a 404.
    assert (await client.get("/backtests/00000000-0000-0000-0000-000000000000",
                             headers=headers)).status_code == 404

    # The run shows up in the tenant's history.
    listing = (await client.get("/backtests", headers=headers)).json()
    assert any(x["id"] == run_id for x in listing)


async def test_backtest_unknown_strategy_404(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post("/backtests", headers=headers, json={"strategy": "NopeNope"})
    assert r.status_code == 404
