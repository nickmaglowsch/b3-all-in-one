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
    from sqlalchemy import delete

    from platform_app.db import async_session_maker
    from platform_app.models import CashEvent, Position, Trade

    # Start from a clean book so total_invested is deterministic even when an
    # earlier test (e.g. the e2e golden import) already populated this dev tenant.
    # Safe because this file runs last alphabetically — nothing depends on it after.
    async with async_session_maker() as session:
        for model in (Trade, Position, CashEvent):
            await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await session.commit()

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


@pytest.mark.market
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
    # Strictly negative, not merely non-positive: a replay starts at zero capital
    # and feeds that through to the daily NAV path the drawdown is computed on, so
    # 0.0 here is the signature of a flattened path rather than a flat market.
    assert sc["max_dd"] < 0

    curve = sc["curve"]
    assert len(curve) > 2
    assert all("date" in p and "aftertax" in p for p in curve)
    assert curve[-1]["aftertax"] == pytest.approx(sc["final_value"])
    assert any(p["aftertax"] > 0 for p in curve)   # the flows actually got deployed
    # The deployed aportes ride along, so the SPA can chart a return, not growth.
    assert all("contribution" in p for p in curve)
    assert any(p["contribution"] > 0 for p in curve)


async def test_read_curve_carries_the_deployed_contribution(tmp_path):  # async: file-wide pytestmark
    """`contribution` is what turns the patrimônio path into a return series in
    the SPA; an artifact without the column still reads, just without it."""
    from platform_app.routes_whatif import _read_curve

    p = tmp_path / "curves.csv"
    p.write_text("date,aftertax,contribution\n"
                 "2024-01-31,10000.0,10000.0\n2024-02-29,100000.0,90000.0\n")
    assert _read_curve(p) == [
        {"date": "2024-01-31", "aftertax": 10000.0, "contribution": 10000.0},
        {"date": "2024-02-29", "aftertax": 100000.0, "contribution": 90000.0},
    ]
    p.write_text("date,aftertax\n2024-01-31,1.0\n")
    assert _read_curve(p) == [{"date": "2024-01-31", "aftertax": 1.0}]
    assert _read_curve(tmp_path / "missing.csv") == []


async def test_fund_from_first_positive_folds_leading_negatives():  # async: file-wide pytestmark
    """The −99% bug: a book whose extract opens with dividends before the buys
    that fund them starts the replay at a negative NAV, which the engine's
    margin-call guard reads as bankruptcy and the UI reports as −99.99%.

    Folding must open the window on the first net-funded date and preserve both
    the total and every later flow untouched."""
    from platform_app.routes_whatif import fund_from_first_positive

    # Shape taken from the real prod book: income lands before the first buy, and
    # the running total crosses into the black in December and back out in January
    # — so anchoring on the FIRST positive prefix would leave the January dip.
    flows = {"2019-11-14": -11.7, "2019-12-12": -10.0, "2019-12-30": 108.0,
             "2020-01-02": -150.0, "2020-03-09": 2151.0}
    out = fund_from_first_positive(flows)

    assert out[0] == {"date": "2020-03-09", "amount": pytest.approx(2087.3)}
    assert [f["date"] for f in out] == ["2020-03-09"]
    # Exact: total_invested is computed off the unfolded dict and must still match.
    assert sum(f["amount"] for f in out) == pytest.approx(sum(flows.values()))
    # Every prefix stays funded — nothing left for the margin-call guard to trip on.
    assert all(cum > 0 for cum in _running(out))

    # An already-funded book is passed through unchanged.
    ok = {"2023-01-16": 25_000.0, "2023-09-11": -9_000.0}
    assert fund_from_first_positive(ok) == [
        {"date": "2023-01-16", "amount": 25_000.0},
        {"date": "2023-09-11", "amount": -9_000.0},
    ]
    # Never net-funded → nothing to replay (the route turns this into a 422).
    assert fund_from_first_positive({"2023-01-16": -5.0, "2023-02-01": -1.0}) == []


def _running(flows: list[dict]) -> list[float]:
    cum, out = 0.0, []
    for f in flows:
        cum += f["amount"]
        out.append(cum)
    return out


async def test_whatif_unknown_strategy_404(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/whatif", json={"strategy_ids": ["NopeNope"]}, headers=headers)
    assert r.status_code == 404


async def test_run_whatif_reads_summary_and_curve_then_cleans_up(monkeypatch, tmp_path):
    """The replay subprocess, faked: argv, the JSON summary, the after-tax curve
    read from the scratch dir, and that dir gone afterwards — on success and on
    an engine failure alike."""
    import subprocess
    from pathlib import Path

    from platform_app import b3run, enginerun, routes_whatif

    monkeypatch.setattr(b3run.settings, "uploads_dir", str(tmp_path))
    seen = {}

    def fake(script, args, extra_env=None):
        seen.update(script=script, args=args, env=extra_env, out=Path(args[1]))
        (Path(args[1]) / "curves.csv").write_text(
            "date,aftertax,pretax\n2024-01-02,100.0,101.0\n")
        return subprocess.CompletedProcess(args, 0, stdout='log\n{"cagr": 0.1}\n', stderr="")

    monkeypatch.setattr(enginerun, "run_script", fake)
    out = routes_whatif._run_whatif("S", {"flows": []}, "t1", {"E": "1"})
    assert out == {"summary": {"cagr": 0.1},
                   "curve": [{"date": "2024-01-02", "aftertax": 100.0}]}
    assert seen["script"] == "run_backtest_json.py"
    assert seen["args"] == ["S", str(seen["out"]), '{"flows": []}']
    assert seen["env"] == {"E": "1"}
    assert seen["out"].parent == b3run.workspace_root("t1")
    assert not seen["out"].exists()

    monkeypatch.setattr(enginerun, "run_script", lambda s, a, e=None:
                        subprocess.CompletedProcess(a, 1, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError, match="^boom$"):
        routes_whatif._run_whatif("S", {}, "t1")
    assert not any(b3run.workspace_root("t1").iterdir())
