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


@pytest.mark.market
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


async def test_get_builtin_spec_for_clone(client):
    """GET /strategies/{name}/spec returns a built-in config strategy's raw spec so
    the frontend can clone it with the FULL factor/universe/selection. Code-kind and
    unknown names → 404."""
    # Built-in rank_and_hold config strategy → 200 with the raw spec dict.
    r = await client.get("/strategies/MomSharpe/spec")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "MomSharpe"
    assert body["kind"] == "rank_and_hold"
    assert body["spec"]["kind"] == "rank_and_hold"
    assert "signal" in body["spec"]  # the factor block a params-only clone would lose

    # A code-kind (StrategyBase) strategy has no data spec → 404.
    assert (await client.get("/strategies/DualMomentum/spec")).status_code == 404

    # Unknown name → 404.
    assert (await client.get("/strategies/NoSuchStrategy/spec")).status_code == 404


# ── The list_*.py shell-outs, faked ───────────────────────────────────────────
# Built-ins, the per-tenant gallery and the signal catalog each run one engine
# listing script whose stdout is a JSON array; a failure is a 500 naming it.

def _fake_list(calls, returncode=0, stdout="[]", stderr=""):
    import subprocess

    def fake(script, args, extra_env=None):
        calls.append((script, args, extra_env))
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
    return fake


@pytest.mark.parametrize("call, script", [
    ("builtins", "list_strategies"), ("signals", "list_signals")])
async def test_a_failed_listing_is_a_500_naming_the_script(monkeypatch, call, script):
    from fastapi import HTTPException

    from platform_app import enginerun, routes

    monkeypatch.setattr(routes, "_STRATEGIES_CACHE", None)
    monkeypatch.setattr(routes, "_SIGNALS_CACHE", None)
    monkeypatch.setattr(enginerun, "run_script", _fake_list([], 1, "out", "err"))
    fn = routes._builtins if call == "builtins" else routes.list_signals
    with pytest.raises(HTTPException) as exc:
        await fn()
    assert exc.value.status_code == 500
    # Named, with a ref into the log — the stderr itself never reaches the client.
    assert exc.value.detail.startswith(f"{script} falhou (ref ")
    assert "err" not in exc.value.detail


async def test_listings_are_parsed_tagged_and_cached(monkeypatch):
    from platform_app import enginerun, routes

    monkeypatch.setattr(routes, "_STRATEGIES_CACHE", None)
    monkeypatch.setattr(routes, "_SIGNALS_CACHE", None)
    calls: list = []
    monkeypatch.setattr(enginerun, "run_script",
                        _fake_list(calls, stdout='[{"name": "B"}]'))
    assert await routes._builtins() == [{"name": "B", "source": "builtin"}]
    assert await routes.list_signals() == [{"name": "B"}]
    await routes._builtins()
    await routes.list_signals()
    assert calls == [("list_strategies.py", [], None), ("list_signals.py", [], None)]


async def test_tenant_gallery_points_the_engine_at_the_users_specs(monkeypatch):
    import uuid

    from fastapi import HTTPException

    from platform_app import enginerun, routes
    from platform_app.db import async_session_maker
    from platform_app.models import UserStrategy

    tid = uuid.uuid4()
    async with async_session_maker() as s:
        s.add(UserStrategy(tenant_id=tid, name="Mine", kind="rank_and_hold", spec={"a": 1}))
        await s.commit()

    calls: list = []
    monkeypatch.setattr(enginerun, "run_script", _fake_list(
        calls, stdout='[{"name": "B"}, {"name": "Mine"}]'))
    async with async_session_maker() as s:
        merged = await routes._gallery(s, tid)
    assert merged == [{"name": "B", "source": "builtin"}, {"name": "Mine", "source": "user"}]
    (script, args, env), = calls
    assert (script, args) == ("list_strategies.py", [])
    assert set(env) == {"ENGINE_USER_SPECS_DIR"}
    assert env["ENGINE_USER_SPECS_DIR"].endswith(f"user_specs/{tid}")

    routes._TENANT_GALLERY.pop(tid, None)
    monkeypatch.setattr(enginerun, "run_script", _fake_list([], 1, "", "bad spec"))
    async with async_session_maker() as s:
        with pytest.raises(HTTPException) as exc:
            await routes._gallery(s, tid)
    assert exc.value.detail.startswith("list_strategies falhou (ref ")


async def test_foreign_junk_and_unknown_strategy_ids_are_the_same_404(client):
    import uuid

    from platform_app.db import async_session_maker
    from platform_app.models import UserStrategy

    h = await _headers(client)
    async with async_session_maker() as s:
        foreign = UserStrategy(tenant_id=uuid.uuid4(), name="Theirs",
                               kind="fixed_weight", spec={})
        s.add(foreign)
        await s.commit()
    for sid in ("junk", str(uuid.uuid4()), str(foreign.id)):
        for method, path, body in (
            ("PUT", f"/strategies/user/{sid}", _rank("Whatever")),
            ("DELETE", f"/strategies/user/{sid}", None),
            ("POST", f"/strategies/user/{sid}/promote", None),
        ):
            r = await client.request(method, path, json=body, headers=h)
            assert (r.status_code, r.json()) == (404, {"detail": "strategy not found"}), (
                method, path)


async def test_the_widened_language_saves_and_round_trips(client):
    h = await _headers(client)
    body = _rank(
        "WideRank", rebalance_months=[12, 6], weighting="expr", weight_expr="1 / vol_60d",
        max_weight=0.1,
        universe={"min_adtv": 1_000_000, "min_price": 1.0,
                  "sectors": {"exclude": ["banc"]}},
        signal={"vars": {"mom": "roll_sum(shift(log_ret, 1), lookback)"}, "lookback": 6,
                "legs": [{"factor": "mom"}, {"factor": "div_yield_12m", "weight": 0.5}],
                "coverage": "any"})
    r = await client.post("/strategies/user", json=body, headers=h)
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert spec["rebalance_months"] == [6, 12]                     # sorted
    assert spec["weight_expr"] == "1 / vol_60d" and spec["max_weight"] == 0.1
    assert spec["universe"]["sectors"] == {"include": [], "exclude": ["banc"]}
    assert spec["signal"]["legs"][1] == {"factor": "div_yield_12m", "weight": 0.5,
                                         "transform": "rank"}
    assert spec["signal"]["coverage"] == "any" and "mom" in spec["signal"]["vars"]

    semi = {"kind": "fixed_weight", "name": "SemiPort", "rebalance": "QE",
            "rebalance_months": [6, 12], "sleeves": [
                {"strategy_ref": "WideRank", "target_pct": 60},
                {"strategy_ref": "CASH_CDI", "target_pct": 40}]}
    r = await client.post("/strategies/user", json=semi, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["rebalance_months"] == [6, 12]


@pytest.mark.parametrize("over", [
    {"rebalance": "QE", "rebalance_months": [1, 7]},              # never a QE row
    {"weighting": "expr"},                                         # no weight_expr
    {"weight_expr": "adtv"},                                       # not weighting expr
    {"max_weight": 0},
    {"signal": {"factor": "momentum", "legs": [{"expr": "ret"}]}},  # two sources
    {"signal": {"legs": [{"expr": "ret", "factor": "momentum"}]}},
    {"signal": {"legs": [{"expr": "ret", "transform": "log"}]}},
    {"market": "US", "signal": {"factor": "div_yield_12m"}},       # engine: B3 only
    {"signal": {"vars": {"rank": "ret"}, "expr": "rank"}},         # engine: shadows rank()
])
async def test_the_widened_language_rejects(client, over):
    h = await _headers(client)
    r = await client.post("/strategies/user", json=_rank("BadWide", **over), headers=h)
    assert r.status_code == 422, r.text


async def test_a_spec_past_the_argv_limit_saves_rather_than_500s(client):
    # 16 vars of 1990 non-ASCII chars: ~200 KB once json.dumps escapes them,
    # which as one argv element failed the validator's exec (E2BIG).
    h = await _headers(client)
    vars_ = {f"v{i}": "é" * 1990 for i in range(16)}
    body = _rank("HugeVars", signal={"vars": vars_, "factor": "momentum", "lookback": 6})
    r = await client.post("/strategies/user", json=body, headers=h)
    assert r.status_code in (200, 422), r.text
