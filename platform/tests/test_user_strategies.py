"""User-created strategies (spec/06): CRUD + validation + gallery merge + one
end-to-end backtest of a user rank strategy (proves materialize → env → registry
→ worker path). Runs against the same dev tenant as the other tenant-plane tests;
each test uses unique strategy names and PUTs its own (atomic-replace) sleeve set.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _headers(client) -> dict:
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _rank(name: str, **over) -> dict:
    spec = {
        "kind": "rank_and_hold", "name": name, "description": "t",
        "rebalance": "ME", "weighting": "equal",
        "universe": {"min_adtv": 1_000_000, "min_price": 1.0},
        "signal": {"factor": "momentum", "lookback": 6, "shift": 1},
        "selection": {"top_pct": 0.2, "min_names": 5},
    }
    spec.update(over)
    return spec


async def test_crud_and_gallery_merge(client):
    h = await _headers(client)

    # Create a rank strategy.
    r = await client.post("/strategies/user", json=_rank("CrudRank"), headers=h)
    assert r.status_code == 200, r.text
    row = r.json()
    sid = row["id"]
    assert row["kind"] == "rank_and_hold"
    assert row["spec"]["signal"]["factor"] == "momentum"
    assert row["warnings"] == []  # price-only, no fundamentals warning

    # Shows in the tenant's user list with the full spec.
    users = (await client.get("/strategies/user", headers=h)).json()
    assert any(x["id"] == sid for x in users)

    # Merged gallery (authed): present + tagged source=user.
    gallery = (await client.get("/strategies", headers=h)).json()
    by = {s["name"]: s for s in gallery}
    assert by["CrudRank"]["source"] == "user"
    assert by["MomSharpe"]["source"] == "builtin"  # built-ins still present

    # Anonymous gallery is built-ins only (no user rows leaked).
    anon = (await client.get("/strategies")).json()
    assert not any(s["name"] == "CrudRank" for s in anon)

    # Update (rename) — cascades over the tenant's live sleeves.
    await client.put("/portfolio/sleeves", headers=h, json=[
        {"strategy_ref": "CrudRank", "target_pct": 50},
        {"strategy_ref": "CASH_CDI", "target_pct": 50}])
    upd = await client.put(f"/strategies/user/{sid}", headers=h, json=_rank("CrudRankRenamed"))
    assert upd.status_code == 200, upd.text
    sleeves = (await client.get("/portfolio/sleeves", headers=h)).json()
    assert {s["strategy_ref"] for s in sleeves} == {"CrudRankRenamed", "CASH_CDI"}

    # Reset sleeves off the strategy so it can be deleted, then delete + cache heals.
    await client.put("/portfolio/sleeves", headers=h, json=[
        {"strategy_ref": "CASH_CDI", "target_pct": 100}])
    d = await client.delete(f"/strategies/user/{sid}", headers=h)
    assert d.status_code == 200, d.text
    gone = (await client.get("/strategies", headers=h)).json()
    assert not any(s["name"] == "CrudRankRenamed" for s in gone)


async def test_rejections(client):
    h = await _headers(client)

    # Duplicate name → 409.
    assert (await client.post("/strategies/user", json=_rank("DupName"), headers=h)).status_code == 200
    assert (await client.post("/strategies/user", json=_rank("DupName"), headers=h)).status_code == 409

    # Built-in name collision → 422.
    assert (await client.post("/strategies/user", json=_rank("MomSharpe"), headers=h)).status_code == 422
    assert (await client.post("/strategies/user", json=_rank("CASH_CDI"), headers=h)).status_code == 422

    # Portfolio: weights not summing to 100 → 422 (pydantic).
    bad_w = {"kind": "fixed_weight", "name": "BadW", "sleeves": [
        {"strategy_ref": "MomSharpe", "target_pct": 60},
        {"strategy_ref": "CASH_CDI", "target_pct": 30}]}
    assert (await client.post("/strategies/user", json=bad_w, headers=h)).status_code == 422

    # Portfolio: unresolvable sleeve ref → 422.
    bad_ref = {"kind": "fixed_weight", "name": "BadRef", "sleeves": [
        {"strategy_ref": "NotARealStrategy", "target_pct": 100}]}
    assert (await client.post("/strategies/user", json=bad_ref, headers=h)).status_code == 422

    # Cycle: a user portfolio may not reference another user portfolio.
    p1 = {"kind": "fixed_weight", "name": "Port1", "sleeves": [
        {"strategy_ref": "MomSharpe", "target_pct": 70},
        {"strategy_ref": "CASH_CDI", "target_pct": 30}]}
    assert (await client.post("/strategies/user", json=p1, headers=h)).status_code == 200
    p2 = {"kind": "fixed_weight", "name": "Port2", "sleeves": [
        {"strategy_ref": "Port1", "target_pct": 50},
        {"strategy_ref": "CASH_CDI", "target_pct": 50}]}
    assert (await client.post("/strategies/user", json=p2, headers=h)).status_code == 422


async def test_sleeves_accept_user_ref_and_delete_referenced(client):
    h = await _headers(client)
    r = await client.post("/strategies/user", json=_rank("RefRank"), headers=h)
    sid = r.json()["id"]

    # A user rank ref is a valid sleeve ref (gallery resolves it).
    ok = await client.put("/portfolio/sleeves", headers=h, json=[
        {"strategy_ref": "RefRank", "target_pct": 60},
        {"strategy_ref": "CASH_CDI", "target_pct": 40}])
    assert ok.status_code == 200, ok.text

    # Delete while referenced by a live sleeve → 409.
    assert (await client.delete(f"/strategies/user/{sid}", headers=h)).status_code == 409

    # Portfolio referencing it → also blocks delete with 409.
    await client.put("/portfolio/sleeves", headers=h, json=[
        {"strategy_ref": "CASH_CDI", "target_pct": 100}])
    port = {"kind": "fixed_weight", "name": "PortRef", "sleeves": [
        {"strategy_ref": "RefRank", "target_pct": 50},
        {"strategy_ref": "CASH_CDI", "target_pct": 50}]}
    pid = (await client.post("/strategies/user", json=port, headers=h)).json()["id"]
    assert (await client.delete(f"/strategies/user/{sid}", headers=h)).status_code == 409

    # Remove both references → delete succeeds.
    await client.delete(f"/strategies/user/{pid}", headers=h)
    assert (await client.delete(f"/strategies/user/{sid}", headers=h)).status_code == 200


async def test_promote_portfolio(client):
    h = await _headers(client)
    port = {"kind": "fixed_weight", "name": "PromoPort", "sleeves": [
        {"strategy_ref": "MomSharpe", "target_pct": 65},
        {"strategy_ref": "CASH_CDI", "target_pct": 35}]}
    pid = (await client.post("/strategies/user", json=port, headers=h)).json()["id"]

    promo = await client.post(f"/strategies/user/{pid}/promote", headers=h)
    assert promo.status_code == 200, promo.text
    live = (await client.get("/portfolio/sleeves", headers=h)).json()
    assert {s["strategy_ref"]: round(s["target_pct"]) for s in live} == {"MomSharpe": 65, "CASH_CDI": 35}

    # Rank strategies cannot be promoted.
    rid = (await client.post("/strategies/user", json=_rank("NotAPort"), headers=h)).json()["id"]
    assert (await client.post(f"/strategies/user/{rid}/promote", headers=h)).status_code == 422


async def test_user_strategy_backtest_e2e(client):
    """Full path: create a price-only user rank strategy, backtest by name, run the
    deferred job inline, assert it reaches done — materialization + ENGINE_USER_SPECS_DIR
    injection + registry pickup + worker all exercised. Needs ENGINE_BENCH_OFFLINE=1
    (set in conftest)."""
    h = await _headers(client)
    r = await client.post("/strategies/user", json=_rank("E2EMom"), headers=h)
    assert r.status_code == 200, r.text

    sub = await client.post("/backtests", headers=h, json={
        "strategy": "E2EMom", "start": "2023-01-01", "end": "2025-12-30", "freq": "ME"})
    assert sub.status_code == 200, sub.text
    run_id = sub.json()["id"]

    from platform_app.tasks import run_backtest
    result = await run_backtest(run_id=run_id)
    assert result["status"] == "done", result

    row = (await client.get(f"/backtests/{run_id}", headers=h)).json()
    assert row["status"] == "done"
    assert row["metrics"]["strategy"] == "E2EMom"
    assert row["metrics"]["final_aftertax_nav"] > 0
