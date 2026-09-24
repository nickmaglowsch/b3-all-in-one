"""What-if portfolio simulation: replay the tenant's REAL dated cash flows through
an alternative strategy, from their first trade to today (spec/05 Feature 2).

"Same money in, different strategy, after-tax." We extract the tenant's real net
external cash flows from their own tables (buys − sells − income) and feed them,
on their true dates, into the engine running each alternative strategy. The
comparison is a point comparison against the real book (total invested + actual
market value) plus each scenario's after-tax equity curve — we do NOT reconstruct
a full historical portfolio index here.

Owned by the what-if agent. Registered in main.py as `whatif_router`.
"""
from __future__ import annotations

import asyncio
import csv
import json
import shutil
import tempfile
from collections import defaultdict
from datetime import date
from itertools import accumulate
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import b3run, enginerun, specstore
from platform_app.db import get_async_session
from platform_app.models import CashEvent, Position, Trade
from platform_app.routes import (
    _REBALANCE,
    _fundamentals_available,
    _gallery,
    _latest_engine_prices,
    _needs_funds_msg,
)
from platform_app.tenancy import get_current_tenant

whatif_router = APIRouter(prefix="/whatif", tags=["whatif"])

# cash_events categories that are a return of/on capital (not new external money).
# Net external flow = buys − sells − income.
INCOME_CATEGORIES = {"income", "amortization"}


# Each id replays the tenant's whole history through a full backtest subprocess,
# serially — so the request's cost is linear in the list the caller sends. Uncapped,
# one authenticated POST with the whole gallery in it occupies the box for minutes.
# ponytail: a cap, not a queue — this is a comparison a person reads, and four
# scenarios is already more than a chart shows well. Move it to the job queue
# (like /backtests) if the comparison ever needs to be wider than that.
MAX_SCENARIOS = 4


class WhatIfRequest(BaseModel):
    strategy_ids: list[str] = Field(max_length=MAX_SCENARIOS)
    # Same enum BacktestRequest uses, and for the same reason: this reaches
    # pandas .resample() in build_shared_data over ~20 years of daily prices. As
    # a free-form string it accepted "s", which asks for a second-resolution
    # frame of hundreds of millions of rows — an OOM of the API container, since
    # unlike a backtest this runs inside the request rather than on the worker.
    freq: _REBALANCE = "ME"


async def _net_flows(session: AsyncSession, tid) -> dict[str, float]:
    """Real dated net external cash flows per ISO date: buys (+) − sells (−)
    from `trades`, minus income from `cash_events`. Same-date flows are summed."""
    trades = (await session.scalars(
        select(Trade).where(Trade.tenant_id == tid)
    )).all()
    events = (await session.scalars(
        select(CashEvent).where(CashEvent.tenant_id == tid)
    )).all()

    flows: dict[str, float] = defaultdict(float)
    for t in trades:
        if not t.date or t.amount is None:
            continue
        if t.side == "Compra":
            flows[t.date.isoformat()] += abs(float(t.amount))
        elif t.side == "Venda":
            flows[t.date.isoformat()] -= abs(float(t.amount))
    for e in events:
        if e.date and e.amount is not None and e.event_category in INCOME_CATEGORIES:
            flows[e.date.isoformat()] -= abs(float(e.amount))
    return {d: v for d, v in flows.items()}


def fund_from_first_positive(flows: dict[str, float]) -> list[dict]:
    """`[{date, amount}, ...]` opening on the date after which the book stays
    net-funded, with everything before it folded into that date. Empty when the
    book is never net-funded.

    A replay starts at zero capital, so the engine's margin-call guard
    (simulation.py: `if pt_nav <= 0 ... break`) ends the whole simulation the
    first time NAV touches zero — and a real B3 extract routinely opens
    *negative*: dividends and small sells on positions bought before the extract
    window land ahead of the buys that fund them. One such book went "bankrupt"
    in its second month and reported −99.99% against R$1,7M invested.

    The anchor is the LAST non-positive prefix, not the first positive one. Those
    early months oscillate — the real book above crossed into the black in
    December and back out in January — so folding at the first crossing leaves a
    later dip for the guard to trip on and fixes nothing. Taking the last one
    makes every remaining prefix positive by construction.

    Folding is exact: the sum is unchanged, so `total_invested` and every later
    flow's date survive. What is dropped is only the window before the extract
    can describe a funded book — dividends on positions bought before it starts.
    """
    items = sorted(flows.items())
    running = list(accumulate(a for _, a in items))
    last_unfunded = max((i for i, c in enumerate(running) if c <= 0), default=-1)
    i = last_unfunded + 1
    if i >= len(items):
        return []
    return [{"date": items[i][0], "amount": running[i]}] + [
        {"date": d, "amount": a} for d, a in items[i + 1:]
    ]


async def _actual_value(session: AsyncSession, tid) -> float:
    """Σ current positions × latest engine price — the real book's mark today
    (same price source as /portfolio/target and the rebalance advisor)."""
    positions = (await session.scalars(
        select(Position).where(Position.tenant_id == tid)
    )).all()
    held = {p.ticker: float(p.qty or 0.0)
            for p in positions if p.ticker and (p.qty or 0.0) != 0}
    prices = dict(await _latest_engine_prices(list(held)))
    return round(sum(qty * prices.get(t, 0.0) for t, qty in held.items()), 2)


def _read_curve(path: Path) -> list[dict]:
    """curves.csv → [{date, aftertax}], the after-tax equity path for the overlay."""
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [{"date": r["date"], "aftertax": float(r["aftertax"])}
                for r in csv.DictReader(fh)]


def _run_whatif(
    strategy: str, config: dict, tid, extra_env: dict | None = None
) -> dict:
    """Run scripts/run_backtest_json.py in the engine venv (mirrors enginerun's
    subprocess pattern; reuses its interpreter + env resolution) and return
    {summary, curve}. The `flows` key in `config` is what makes it a replay rather
    than a plain backtest — same script, same artifact contract.
    Artifacts land in a scratch dir we read then delete — under the tenant's own
    uploads dir so a skipped `finally` leaves nothing erasure can't reach (see
    b3run.workspace_root). Blocking — call via to_thread."""
    out = Path(tempfile.mkdtemp(prefix="b3whatif-", dir=b3run.workspace_root(tid)))
    try:
        summary = enginerun.run_json(
            "run_backtest_json.py", [strategy, str(out), json.dumps(config)], extra_env)
        return {"summary": summary, "curve": _read_curve(out / "curves.csv")}
    finally:
        shutil.rmtree(out, ignore_errors=True)


@whatif_router.post("")
async def create_whatif(
    body: WhatIfRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Replay the tenant's real cash flows through each requested strategy and
    return a point comparison (invested + actual value) plus per-scenario
    after-tax curves and metrics."""
    _user, tid = tenant
    if not body.strategy_ids:
        raise HTTPException(422, "strategy_ids must be non-empty")
    gallery = {s["name"]: s for s in await _gallery(session, tid)}
    for sid in body.strategy_ids:
        if sid not in gallery:
            raise HTTPException(404, f"unknown strategy: {sid}")
    needs = [s for s in body.strategy_ids if gallery[s].get("needs_fundamentals")]
    if needs and not await _fundamentals_available():
        raise HTTPException(422, _needs_funds_msg(needs))

    flows = await _net_flows(session, tid)
    if not flows:
        raise HTTPException(422, "no trades to replay — import a portfolio first")
    flows_list = fund_from_first_positive(flows)
    if not flows_list:
        raise HTTPException(
            422, "sem aporte líquido para replicar — as saídas cobrem todas as entradas")
    total_invested = round(sum(flows.values()), 2)
    start = flows_list[0]["date"]  # the date the book is first net-funded

    extra_env = await specstore.user_specs_env(session, tid) or None
    scenarios = []
    for sid in body.strategy_ids:
        config = {"flows": flows_list, "start": start, "freq": body.freq}
        try:
            out = await asyncio.to_thread(_run_whatif, sid, config, tid, extra_env)
        except RuntimeError as e:
            raise HTTPException(500, f"whatif ({sid}) failed: {e}")
        m = out["summary"]
        aft = m.get("aftertax") or {}
        scenarios.append({
            "strategy": sid,
            "final_value": round(float(m.get("final_aftertax_nav", 0.0)), 2),
            "ann_return": aft.get("Ann. Return (%)"),
            "sharpe": aft.get("Sharpe"),
            "max_dd": aft.get("Max Drawdown (%)"),
            "curve": out["curve"],
        })

    return {
        "as_of": date.today().isoformat(),
        "total_invested": total_invested,
        "actual_value": await _actual_value(session, tid),
        "scenarios": scenarios,
    }
