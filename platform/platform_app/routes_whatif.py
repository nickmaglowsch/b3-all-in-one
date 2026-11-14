"""What-if portfolio simulation: replay the tenant's REAL dated cash flows through
an alternative strategy, from their first trade to today (spec/05 Feature 2).

"Same money in, different strategy, after-tax." We extract the tenant's real net
external cash flows from their own tables (buys − sells − income) and feed them,
on their true dates, into the engine running each alternative strategy. The
comparison is a point comparison against the real book (total invested + actual
market value) plus each scenario's after-tax equity curve — we do NOT reconstruct
a full historical portfolio index here.

Each scenario is a full backtest (6-20 s), so POST /whatif only validates and
queues a run (whatif_models.WhatIfRun); the default worker replays it
(tasks_whatif → replay below) and the page polls GET /whatif/{id}.

Owned by the what-if agent. Registered in main.py as `whatif_router`.
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import json
import shutil
import tempfile
from collections import defaultdict
from datetime import date
from itertools import accumulate
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import b3run, enginerun, specstore, subprocs, tasks
from platform_app.db import get_async_session, xact_lock
from platform_app.jobstatus import WHATIF_INFLIGHT, WhatIfStatus
from platform_app.models import CashEvent, Portfolio, Position, Trade
from platform_app.whatif_models import WhatIfRun
from platform_app.routes import (
    _REBALANCE,
    _fundamentals_available,
    _gallery,
    _latest_engine_prices,
    _needs_funds_msg,
)
from platform_app.tenancy import get_current_tenant, get_owned

whatif_router = APIRouter(prefix="/whatif", tags=["whatif"])

# cash_events categories that are a return of/on capital (not new external money).
# Net external flow = buys − sells − income.
INCOME_CATEGORIES = {"income", "amortization"}


# Each id replays the tenant's whole history through a full backtest, serially
# in one worker job — so a run's cost is linear in the list the caller sends, and
# four scenarios is already more than a chart shows well.
MAX_SCENARIOS = 4
# Runs one ACCOUNT may have queued or running at once: each holds one of the
# default worker's two slots for up to four backtests, the slots imports and the
# bench share. Same reasoning as routes.BACKTEST_QUOTA.
MAX_INFLIGHT = 2
# Finished runs kept per book (_forget_old_runs).
KEEP_RUNS = 20


class WhatIfRequest(BaseModel):
    strategy_ids: list[str] = Field(max_length=MAX_SCENARIOS)
    # Same enum BacktestRequest uses, and for the same reason: this reaches
    # pandas .resample() in build_shared_data over ~20 years of daily prices. As
    # a free-form string it accepted "s", which asks for a second-resolution
    # frame of hundreds of millions of rows — an OOM of the worker that runs it.
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
    """curves.csv → [{date, aftertax, contribution}], the after-tax equity path
    for the overlay.

    `contribution` is the cash the engine deployed on that row — the replayed
    aportes. The path itself is patrimônio, so it grows with every deposit; the
    SPA needs the deposits to read it as a return (time-weighted, the same
    (V_t − C_t) / V_{t−1} chain the engine's own metrics use) instead of
    calling R$90k of aportes "+900%". Omitted when the artifact has no such
    column."""
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        rows = []
        for r in csv.DictReader(fh):
            point = {"date": r["date"], "aftertax": float(r["aftertax"])}
            if r.get("contribution"):
                point["contribution"] = float(r["contribution"])
            rows.append(point)
        return rows


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
        # stdin, not argv: `flows` has an entry per distinct day of the tenant's
        # history, and a long one outgrew the 128 KB one argv element may hold.
        summary = enginerun.run_json(
            "run_backtest_json.py", [strategy, str(out), "-"], extra_env,
            stdin=json.dumps(config))
        return {"summary": summary, "curve": _read_curve(out / "curves.csv")}
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _run_dict(r: WhatIfRun) -> dict:
    """The row as the page polls it; `result` is the old synchronous response."""
    return {
        "id": str(r.id), "status": r.status, "error": subprocs.redact(r.error),
        "strategy_ids": (r.params or {}).get("strategy_ids", []),
        "freq": (r.params or {}).get("freq"), "result": r.result,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


async def inflight_whatifs(session: AsyncSession, user) -> int:
    """Runs not yet finished across every book the account owns — the same
    shape, window and reasoning as routes.inflight_backtests."""
    managed = select(Portfolio.id).where(Portfolio.owner_id == user.id)
    return await session.scalar(
        select(func.count()).select_from(WhatIfRun).where(
            WhatIfRun.status.in_(WHATIF_INFLIGHT),
            WhatIfRun.created_at > func.now() - datetime.timedelta(days=1),
            (WhatIfRun.tenant_id == user.id) | WhatIfRun.tenant_id.in_(managed),
        ))


async def _forget_old_runs(session: AsyncSession, tid) -> None:
    """Keep a book's KEEP_RUNS newest runs. Each holds the book's dated flows
    and up to four curves; nothing lists the old ones, so they are only weight."""
    keep = select(WhatIfRun.id).where(WhatIfRun.tenant_id == tid) \
        .order_by(WhatIfRun.created_at.desc()).limit(KEEP_RUNS)
    await session.execute(delete(WhatIfRun).where(
        WhatIfRun.tenant_id == tid, WhatIfRun.status.not_in(WHATIF_INFLIGHT),
        WhatIfRun.id.not_in(keep)))


@whatif_router.post("", status_code=202)
async def create_whatif(
    body: WhatIfRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Queue a replay of the tenant's real cash flows through each requested
    strategy; poll GET /whatif/{id} for the point comparison (invested + actual
    value) and per-scenario after-tax curves and metrics.

    Everything that can be refused is refused here, synchronously — an unknown
    strategy, missing fundamentals, a book with nothing to replay — so a queued
    run is one the engine can actually do."""
    user, tid = tenant
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

    # Held to the commit, so parallel POSTs cannot all count `limit - 1`.
    await xact_lock(session, "whatif", user.id)
    running = await inflight_whatifs(session, user)
    if running >= MAX_INFLIGHT:
        raise HTTPException(429, detail={
            "error": "whatif_quota", "limit": MAX_INFLIGHT, "running": running,
            "message": f"{running} simulações já na fila — aguarde uma terminar."})
    await _forget_old_runs(session, tid)
    run = WhatIfRun(tenant_id=tid, status=WhatIfStatus.PENDING, params={
        "strategy_ids": body.strategy_ids, "freq": body.freq,
        "flows": flows_list, "start": flows_list[0]["date"],  # first net-funded date
        "total_invested": round(sum(flows.values()), 2)})
    session.add(run)
    await session.commit()

    from platform_app import tasks_whatif  # it imports this module
    await tasks.defer_or_fail(
        session, [run], "whatif", lambda: tasks_whatif.run_whatif.defer_async(run_id=str(run.id)),
        WhatIfStatus.PENDING, status=WhatIfStatus.ERROR, finished_at=func.now())
    return _run_dict(run)


@whatif_router.get("/{run_id}")
async def get_whatif(
    run_id: str,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return _run_dict(await get_owned(session, WhatIfRun, run_id, tid, "simulação não encontrada"))


async def replay(session: AsyncSession, tid, params: dict) -> dict:
    """Run every scenario of a queued what-if; the result the page reads.
    For tasks_whatif, in the worker: the engine runs are full backtests.
    RuntimeError names the scenario that failed (subprocs.report's ref)."""
    extra_env = await specstore.user_specs_env(session, tid) or None
    await session.commit()  # no connection held across up to four engine runs
    config = {"flows": params["flows"], "start": params["start"], "freq": params["freq"]}
    scenarios = []
    for sid in params["strategy_ids"]:
        try:
            out = await asyncio.to_thread(_run_whatif, sid, config, tid, extra_env)
        except RuntimeError as e:
            raise RuntimeError(subprocs.report(f"whatif ({sid})", str(e))) from None
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
        "total_invested": params["total_invested"],
        "actual_value": await _actual_value(session, tid),
        "scenarios": scenarios,
    }
