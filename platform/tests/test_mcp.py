"""The bench over MCP (spec/09): tokens, the JSON-RPC transport, and every tool.

Each account here is its own (conftest.auth), so the token, strategy and run
listings asserted on cannot be disturbed by another test's rows. The engine's
listing/validation scripts really run (the engine venv exists in CI); only
backtests themselves stay pending, because no worker runs in tests — the
results tools are exercised on a run seeded the way the worker would finish it.
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")

_NEW = {"protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"}}


def _rank(name: str, **over) -> dict:
    spec = {
        "kind": "rank_and_hold", "name": name, "description": "made by a test",
        "rebalance": "ME", "weighting": "equal",
        "universe": {"min_adtv": 1_000_000, "min_price": 1.0},
        "signal": {"factor": "momentum", "lookback": 6},
        "selection": {"top_n": 15, "min_names": 5},
    }
    spec.update(over)
    return spec


async def _token(client, headers, name="claude") -> str:
    r = await client.post("/mcp/tokens", json={"name": name}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _rpc(client, token, method, params=None, mid=1, status=200):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    r = await client.post("/mcp", json=msg, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status, r.text
    return r.json()


async def _call(client, token, tool, **arguments):
    """tools/call → (payload, is_error). JSON payloads come back parsed."""
    res = (await _rpc(client, token, "tools/call",
                      {"name": tool, "arguments": arguments}))["result"]
    text = res["content"][0]["text"]
    try:
        return json.loads(text), res["isError"]
    except ValueError:
        return text, res["isError"]


async def test_token_lifecycle(client):
    h = await auth(client, "mcp-tokens@example.com", modules=["bench"])
    r = await client.post("/mcp/tokens", json={"name": "Claude Code"}, headers=h)
    assert r.status_code == 200, r.text
    made = r.json()
    raw = made["token"]
    assert raw.startswith("qtx_mcp_") and raw.startswith(made["prefix"])

    # Listed without the secret; the stored hash never leaves either.
    listed = (await client.get("/mcp/tokens", headers=h)).json()
    assert [t["id"] for t in listed] == [made["id"]]
    assert "token" not in listed[0] and "token_hash" not in listed[0]

    assert (await _rpc(client, raw, "ping"))["result"] == {}
    listed = (await client.get("/mcp/tokens", headers=h)).json()
    assert listed[0]["last_used_at"] is not None

    # Revoked → the very next call is refused.
    assert (await client.delete(f"/mcp/tokens/{made['id']}", headers=h)).status_code == 200
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                          headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    # Someone else's / unknown id → the same 404.
    assert (await client.delete(f"/mcp/tokens/{uuid.uuid4()}", headers=h)).status_code == 404


async def test_tokens_need_bench_and_are_capped(client):
    h = await auth(client, "mcp-nobench@example.com", modules=["irpf"])
    assert (await client.post("/mcp/tokens", json={"name": "x"}, headers=h)).status_code == 402

    h = await auth(client, "mcp-cap@example.com", modules=["bench"])
    for i in range(10):
        await _token(client, h, f"t{i}")
    assert (await client.post("/mcp/tokens", json={"name": "t10"}, headers=h)).status_code == 409


async def test_auth_refusals(client):
    h = await auth(client, "mcp-auth@example.com", modules=["bench"])
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert (await client.post("/mcp", json=msg)).status_code == 401
    # A session JWT is not an MCP token.
    assert (await client.post("/mcp", json=msg, headers=h)).status_code == 401
    bogus = {"Authorization": "Bearer qtx_mcp_not-a-real-token"}
    assert (await client.post("/mcp", json=msg, headers=bogus)).status_code == 401

    token = await _token(client, h)
    ok = {"Authorization": f"Bearer {token}"}
    r = await client.post("/mcp", json=msg, headers={**ok, "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = await client.post("/mcp", json=msg, headers={**ok, "MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 400
    assert (await client.get("/mcp", headers=ok)).status_code == 405
    assert (await client.delete("/mcp", headers=ok)).status_code == 405


async def test_handshake_and_protocol(client):
    from platform_app.mcp_schemas import TOOLS
    from platform_app.mcp_tools import HANDLERS

    h = await auth(client, "mcp-proto@example.com", modules=["bench"])
    token = await _token(client, h)
    bearer = {"Authorization": f"Bearer {token}"}

    init = (await _rpc(client, token, "initialize", _NEW))["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "quantax-bench"
    assert "get_authoring_guide" in init["instructions"]
    # A version we don't speak → our newest, and the client decides.
    odd = (await _rpc(client, token, "initialize", {**_NEW, "protocolVersion": "2030-01-01"}))
    assert odd["result"]["protocolVersion"] == "2025-11-25"

    # Notifications get no body.
    r = await client.post("/mcp", headers=bearer,
                          json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.content == b""

    tools = (await _rpc(client, token, "tools/list"))["result"]["tools"]
    assert {t["name"] for t in tools} == set(HANDLERS) == {t["name"] for t in TOOLS}
    assert all(t["inputSchema"]["type"] == "object" for t in tools)

    assert (await _rpc(client, token, "nope"))["error"]["code"] == -32601
    bad = await _rpc(client, token, "tools/call", {"name": "nope", "arguments": {}})
    assert bad["error"]["code"] == -32602
    bad = await _rpc(client, token, "tools/call", {"name": "list_signals", "arguments": [1]})
    assert bad["error"]["code"] == -32602
    assert (await _rpc(client, token, "ping", params=[1]))["error"]["code"] == -32602

    # Batches answer in order and skip notifications; junk is a JSON-RPC error.
    r = await client.post("/mcp", headers=bearer, json=[
        {"jsonrpc": "2.0", "id": "a", "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
        {"jsonrpc": "1.0", "id": "b", "method": "ping"},
        {"jsonrpc": "2.0", "id": "c", "result": {}},
    ])
    assert [m["id"] for m in r.json()] == ["a", "b"]
    assert r.json()[1]["error"]["code"] == -32600
    r = await client.post("/mcp", headers={**bearer, "Content-Type": "application/json"},
                          content=b"{not json")
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700
    r = await client.post("/mcp", headers=bearer, json=[])
    assert r.status_code == 400
    r = await client.post("/mcp", headers={**bearer, "Content-Type": "application/json"},
                          content=b" " * (1024 * 1024 + 1))
    assert r.status_code == 413


async def test_strategy_tools(client):
    h = await auth(client, "mcp-strats@example.com", modules=["bench"])
    token = await _token(client, h)

    guide, err = await _call(client, token, "get_authoring_guide")
    assert not err and "rank_and_hold" in guide and "nan_add" in guide

    signals, err = await _call(client, token, "list_signals")
    assert not err and "momentum" in {s["name"] for s in signals}

    gallery, err = await _call(client, token, "list_strategies", source="builtin", market="B3")
    assert not err and "MomSharpe" in {s["name"] for s in gallery}
    assert all(s["market"] == "B3" and "params" not in s for s in gallery)
    with_params, _ = await _call(client, token, "list_strategies", include_params=True)
    assert any(s["params"] for s in with_params)

    spec, err = await _call(client, token, "get_strategy_spec", name="MomSharpe")
    assert not err and spec["spec"]["kind"] == "rank_and_hold"
    missing, err = await _call(client, token, "get_strategy_spec", name="NoSuchThing")
    assert err and missing.startswith("404")

    # Validation reports, never saves.
    bad, err = await _call(client, token, "validate_strategy", spec={"kind": "rank_and_hold"})
    assert err and "invalid spec" in bad
    bad, err = await _call(client, token, "validate_strategy",
                           spec=_rank("Bad", signal={"expr": "import_os(ret)"}))
    assert err and bad.startswith("422")
    ok, err = await _call(client, token, "validate_strategy", spec=_rank("AI Mom"))
    assert not err and ok["ok"] and ok["compiled_spec"]["selection"]["top_n"] == 15
    assert (await _call(client, token, "list_my_strategies"))[0] == []

    # Create → it is the user's, marked as the AI's, visible to /bench.
    made, err = await _call(client, token, "create_strategy", spec=_rank("AI Mom"))
    assert not err and made["origin"] == "mcp", made
    rows = (await client.get("/strategies/user", headers=h)).json()
    assert [(r["name"], r["origin"]) for r in rows] == [("AI Mom", "mcp")]
    gallery = (await client.get("/strategies", headers=h)).json()
    assert {"name": "AI Mom", "source": "user"}.items() <= next(
        s for s in gallery if s["name"] == "AI Mom").items()
    dup, err = await _call(client, token, "create_strategy", spec=_rank("AI Mom"))
    assert err and dup.startswith("409")
    warned, _ = await _call(client, token, "validate_strategy", spec=_rank("AI Mom"))
    assert any("already have" in w for w in warned["warnings"])
    clash, err = await _call(client, token, "create_strategy", spec=_rank("MomSharpe"))
    assert err and clash.startswith("422")

    # Its own spec reads back without an engine round-trip.
    mine, _ = await _call(client, token, "get_strategy_spec", name="AI Mom")
    assert mine["source"] == "user" and mine["spec"]["signal"]["lookback"] == 6

    # Update by name, then by id; the new name is what lists.
    upd, err = await _call(client, token, "update_strategy", strategy="AI Mom",
                           spec=_rank("AI Mom 9m", signal={"factor": "momentum", "lookback": 9}))
    assert not err and upd["name"] == "AI Mom 9m"
    upd, err = await _call(client, token, "update_strategy", strategy=made["id"],
                           spec=_rank("AI Mom 9m", description="second pass"))
    assert not err and upd["spec"]["description"] == "second pass"
    nope, err = await _call(client, token, "update_strategy", strategy="ghost", spec=_rank("x"))
    assert err and "list_my_strategies" in nope

    gone, err = await _call(client, token, "delete_strategy", strategy="AI Mom 9m")
    assert not err and gone == {"deleted": "AI Mom 9m"}
    assert (await client.get("/strategies/user", headers=h)).json() == []


async def test_regime_and_market_survive_the_save(client):
    """The bench form has always sent `regime`; the request model dropped it."""
    h = await auth(client, "mcp-regime@example.com", modules=["bench"])
    regime = {"inputs": {"trend": {"flag": "bench_above", "offset": 1}},
              "rules": [{"trend": True}, {"default": True, "park": True}]}
    r = await client.post("/strategies/user", headers=h,
                          json=_rank("Gated", regime=regime, market="US",
                                     universe={"min_adtv": 1000000, "min_price": 5.0}))
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["regime"] == regime
    assert r.json()["spec"]["market"] == "US"
    r = await client.post("/strategies/user", headers=h,
                          json=_rank("Bad regime", regime={"inputs": {}}))
    assert r.status_code == 422
    r = await client.post("/strategies/user", headers=h, json=_rank("Mars", market="MARS"))
    assert r.status_code == 422


async def test_exposure_zones_and_a_ticker_allow_list_reach_the_engine(client):
    """A drawdown-zoned BTC tilt: `exposure` rules on `bench_drawdown` save as
    written; `universe.tickers` survives the request model and is checked against
    the plane on save — here an empty one, so it is refused, naming the field,
    rather than saved as a book that would sit in CDI for its whole history."""
    h = await auth(client, "mcp-exposure@example.com", modules=["bench"])
    regime = {"add_ibov": False,
              "inputs": {"deep": {"flag": "bench_drawdown", "offset": 1, "gt": 0.5}},
              "rules": [{"deep": True, "exposure": 1.0},
                        {"default": True, "exposure": 0.25}]}
    r = await client.post("/strategies/user", headers=h, json=_rank(
        "BTC tilt", market="CRYPTO", regime=regime,
        universe={"min_adtv": 1000000, "min_price": 0}))
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["regime"] == regime

    r = await client.post("/strategies/user", headers=h, json=_rank(
        "BTC only", market="CRYPTO", regime=regime,
        universe={"min_adtv": 0, "min_price": 0, "tickers": ["BTCUSDT"]}))
    assert r.status_code == 422 and "universe.tickers" in r.json()["detail"], r.text

    r = await client.post("/strategies/user", headers=h, json=_rank(
        "Too much", market="CRYPTO",
        regime={"inputs": {}, "rules": [{"default": True, "exposure": 1.5}]}))
    assert r.status_code == 422 and "exposure" in r.json()["detail"], r.text


async def _seed_done_run(session_maker, tid, strategy, sharpe, ann, tmp: Path) -> str:
    from platform_app.models import BacktestRun

    def block(label):
        return {"Strategy": label, "Ann. Return (%)": ann, "Ann. Volatility (%)": 15.0,
                "Sharpe": sharpe, "Max Drawdown (%)": -20.0, "Calmar": 0.5}

    rid = uuid.uuid4()
    out = tmp / str(rid)
    out.mkdir(parents=True)
    (out / "curves.csv").write_text(
        "date,pretax,aftertax,drawdown,turnover,contribution,tax_paid,loss_carryforward\n"
        "2020-01-31,100,100,0,1,0,0,0\n2020-12-31,120,115,0,0,0,0,0\n"
        "2021-06-30,130,124,0,0,10,0,0\n2021-12-31,140,132,0,0,0,0,0\n")
    async with session_maker() as s:
        s.add(BacktestRun(
            id=rid, tenant_id=tid, strategy=strategy, status="done",
            params={"params": {"lookback": 6}, "start": "2020-01-01", "end": None, "freq": "ME"},
            metrics={"strategy": strategy, "start": "2020-01-01", "end": "2021-12-31",
                     "freq": "ME", "market": "B3", "periods": 4, "data_warnings": [],
                     "invested_from": "2020-01-31",
                     "total_tax_paid": 1.0, "final_aftertax_nav": 132.0,
                     "pretax": block("Pre-Tax"), "aftertax": block("After-Tax")},
            artifacts_path=str(out),
            finished_at=datetime.datetime.now(datetime.timezone.utc)))
        await s.commit()
    return str(rid)


async def test_backtest_tools(client, tmp_path):
    from platform_app.db import async_session_maker

    h = await auth(client, "mcp-runs@example.com", modules=["bench"])
    tid = uuid.UUID((await client.get("/me", headers=h)).json()["tenant_id"])
    token = await _token(client, h)

    queued, err = await _call(client, token, "run_backtest", strategy="MomSharpe",
                              start="2023-01-01", end="2024-12-31", wait_seconds=0)
    assert not err and queued["status"] == "pending", queued
    assert queued["bench_url"].endswith(f"/bench/run?id={queued['run_id']}")
    assert "get_backtest" in queued["next"]
    unknown, err = await _call(client, token, "run_backtest", strategy="Nope", wait_seconds=0)
    assert err and unknown.startswith("404")
    bad, err = await _call(client, token, "run_backtest", strategy="MomSharpe", freq="s")
    assert err and "invalid backtest request" in bad
    bad, err = await _call(client, token, "run_backtest", strategy="MomSharpe", wait_seconds="x")
    assert err

    # Waiting on a run nobody works on returns when the wait runs out.
    still, err = await _call(client, token, "get_backtest", run_id=queued["run_id"],
                             wait_seconds=1)
    assert not err and still["status"] == "pending"
    waited, err = await _call(client, token, "run_backtest", strategy="MomSharpe", wait_seconds=1)
    assert not err and waited["status"] == "pending" and waited["run_id"] != queued["run_id"]

    good = await _seed_done_run(async_session_maker, tid, "MomSharpe", 0.9, 12.0, tmp_path)
    meh = await _seed_done_run(async_session_maker, tid, "LowVol", 0.3, 8.0, tmp_path)

    board, err = await _call(client, token, "list_backtests", sort_by="sharpe")
    assert not err
    assert [r["run_id"] for r in board][:2] == [good, meh]
    assert {r["status"] for r in board[2:]} == {"pending"}  # no metric → last
    assert board[0]["aftertax"]["sharpe"] == 0.9 and board[0]["window"]["end"] == "2021-12-31"
    assert board[0]["window"]["invested_from"] == "2020-01-31"
    only, _ = await _call(client, token, "list_backtests", strategy="LowVol")
    assert [r["run_id"] for r in only] == [meh]
    _, err = await _call(client, token, "list_backtests", sort_by="vibes")
    assert err

    one, err = await _call(client, token, "get_backtest", run_id=good, full_metrics=True)
    assert not err and one["pretax"]["ann_return"] == 12.0
    assert one["metrics"]["final_aftertax_nav"] == 132.0 and "next" not in one

    curve, err = await _call(client, token, "get_backtest_curve", run_id=good, max_points=3)
    assert not err and curve["periods"] == 4
    assert curve["curve"]["date"] == ["2020-01-31", "2021-06-30", "2021-12-31"]
    # 2021: 115 → 132 with 10 of it contributed in June = 7/115.
    assert curve["yearly_returns_pct"]["aftertax"] == {"2020": 15.0, "2021": 6.09}

    missing, err = await _call(client, token, "get_backtest", run_id=str(uuid.uuid4()))
    assert err and missing.startswith("404")
    unfinished, err = await _call(client, token, "get_backtest_curve", run_id=queued["run_id"])
    assert err and unfinished.startswith("409")


async def test_lapsed_bench_stops_the_token(client):
    h = await auth(client, "mcp-lapse@example.com", modules=["bench"])
    token = await _token(client, h)
    r = await client.post("/billing/dev-modules", json={"modules": ["irpf"]}, headers=h)
    assert r.status_code == 200
    out, err = await _call(client, token, "list_signals")
    assert err and out.startswith("402")


async def test_client_book_token_needs_empresarial(client):
    h = await auth(client, "mcp-firm@example.com", modules=["empresarial"])
    book = (await client.post("/portfolios", json={"label": "Cliente MCP"}, headers=h)).json()
    token = await _token(client, {**h, "X-Portfolio-Id": book["id"]})
    made, err = await _call(client, token, "create_strategy", spec=_rank("Client AI"))
    assert not err
    # The strategy lives in the client's book, not the firm's own.
    own = (await client.get("/strategies/user", headers=h)).json()
    theirs = (await client.get("/strategies/user",
                               headers={**h, "X-Portfolio-Id": book["id"]})).json()
    assert own == [] and [s["name"] for s in theirs] == ["Client AI"]

    await client.post("/billing/dev-modules", json={"modules": ["bench"]}, headers=h)
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 402


async def test_results_helpers_edges():
    from platform_app.mcp_results import ToolError, _sample, _yearly, leaderboard, summarize

    # A run stored before the engine reported invested_from says null, not a date.
    old = summarize({"id": "r", "strategy": "A", "status": "done",
                     "metrics": {"start": "2019-01-01", "pretax": {}}})
    assert old["window"]["invested_from"] is None

    assert _sample(3, 10) == [0, 1, 2]
    assert _sample(100, 5)[-1] == 99 and len(_sample(100, 5)) == 5
    # A NaN cell (None after the curves route) or a zero start reads as unknown.
    assert _yearly(["2020-01-31", "2020-12-31"], [100.0, None], None) == {"2020": None}
    assert _yearly(["2020-01-31", "2020-12-31"], [0.0, 5.0], [0, 0]) == {"2020": None}
    assert _yearly(["2020-01-31"], [], None) == {}
    rows = [{"strategy": "A", "status": "done", "aftertax": {"volatility": 20.0}},
            {"strategy": "B", "status": "done", "aftertax": {"volatility": 10.0}}]
    assert [r["strategy"] for r in leaderboard(rows, sort_by="volatility")] == ["B", "A"]
    for bad in ({"basis": "nominal"}, {"limit": "many"}):
        with pytest.raises(ToolError):
            leaderboard(rows, **bad)
