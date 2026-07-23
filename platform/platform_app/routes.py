"""Tenant-scoped data-plane endpoints: imports, portfolio, IRPF.

Every read is scoped by tenant_id from get_current_tenant — never trust an id in the
request. Routers + task functions only (no service/repository layers).
"""
from __future__ import annotations

import asyncio
import csv
import json
import shutil
import tempfile
import uuid
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import b3run, enginerun, tasks
from platform_app.config import settings
from platform_app.db import get_async_session
from platform_app.models import (
    BacktestRun, CashEvent, CostBasisOverride, Import, Position, Trade,
)
from platform_app.tenancy import get_current_tenant

imports_router = APIRouter(prefix="/imports", tags=["imports"])
portfolio_router = APIRouter(prefix="/portfolio", tags=["portfolio"])
irpf_router = APIRouter(prefix="/irpf", tags=["irpf"])
strategies_router = APIRouter(prefix="/strategies", tags=["backtests"])
backtests_router = APIRouter(prefix="/backtests", tags=["backtests"])
market_router = APIRouter(prefix="/market", tags=["market"])


def _detect_kind(name: str) -> str:
    low = name.lower()
    if low.startswith("negociacao-"):
        return "negociacao"
    if low.startswith("movimentacao-"):
        return "movimentacao"
    return "unknown"


@imports_router.post("")
async def create_imports(
    files: list[UploadFile],
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    if not files:
        raise HTTPException(400, "no files uploaded")
    for f in files:
        if not (f.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(400, f"only .xlsx accepted: {f.filename}")

    dest = Path(settings.uploads_dir) / str(tid)
    dest.mkdir(parents=True, exist_ok=True)
    created: list[Import] = []
    for f in files:
        path = dest / f.filename
        path.write_bytes(await f.read())
        imp = Import(
            tenant_id=tid, filename=f.filename, stored_path=str(path),
            kind=_detect_kind(f.filename), status="pending",
        )
        session.add(imp)
        created.append(imp)
    await session.commit()

    # One job for the whole batch (load_b3 is a full rebuild over all files at once).
    # ponytail: per-request procrastinate pool — imports are rare human actions.
    async with tasks.app.open_async():
        await tasks.process_imports.defer_async(tenant_id=str(tid))

    return [
        {"id": str(i.id), "filename": i.filename, "kind": i.kind, "status": i.status}
        for i in created
    ]


@imports_router.get("")
async def list_imports(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (
        await session.scalars(
            select(Import).where(Import.tenant_id == tid).order_by(Import.created_at)
        )
    ).all()
    return [
        {
            "id": str(i.id), "filename": i.filename, "kind": i.kind, "status": i.status,
            "error": i.error, "parser_version": i.parser_version,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in rows
    ]


@portfolio_router.get("/positions")
async def get_positions(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (
        await session.scalars(
            select(Position).where(Position.tenant_id == tid).order_by(Position.ticker)
        )
    ).all()
    return [
        {"ticker": p.ticker, "qty": p.qty, "avg_cost": p.avg_cost,
         "total_cost": p.total_cost, "market": p.market}
        for p in rows
    ]


@portfolio_router.get("/trades")
async def get_trades(
    limit: int = 50,
    offset: int = 0,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    limit = max(1, min(limit, 500))
    total = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.tenant_id == tid)
    )
    rows = (
        await session.scalars(
            select(Trade)
            .where(Trade.tenant_id == tid)
            .order_by(Trade.date.desc(), Trade.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        {"date": t.date.isoformat() if t.date else None, "side": t.side,
         "market": t.market, "broker": t.broker, "ticker": t.ticker,
         "qty": t.qty, "price": t.price, "amount": t.amount,
         "signed_qty": t.signed_qty, "source_file": t.source_file}
        for t in rows
    ]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@portfolio_router.get("/income")
async def get_income(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    year = func.extract("year", CashEvent.date)
    q = (
        select(CashEvent.event_category, year.label("year"),
               func.sum(CashEvent.amount).label("total"))
        .where(CashEvent.tenant_id == tid)
        .group_by(CashEvent.event_category, year)
        .order_by(year, CashEvent.event_category)
    )
    rows = await session.execute(q)
    return [
        {"event_category": ec, "year": int(y), "total": float(t or 0.0)}
        for ec, y, t in rows
        if y is not None
    ]


class IrpfRequest(BaseModel):
    year: int = 2025


# IRPF export headers must mirror the canonical CSVs byte-for-byte so irpf_2026 reads
# them exactly as it reads taxcore/data/*.csv (source_file -> __source_file column).
_TRADE_HEADER = ["date", "side", "market", "maturity", "broker", "ticker",
                 "qty", "price", "amount", "__source_file", "signed_qty"]
_EVENT_HEADER = ["date", "ticker", "event_type", "event_category", "is_amortization",
                 "qty", "price", "amount", "broker", "__source_file"]
_OVERRIDE_HEADER = ["date", "ticker", "kind", "qty", "price", "amount", "note"]


@irpf_router.post("/reports")
async def irpf_report(
    body: IrpfRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    trades = (await session.scalars(
        select(Trade).where(Trade.tenant_id == tid).order_by(Trade.id)
    )).all()
    events = (await session.scalars(
        select(CashEvent).where(CashEvent.tenant_id == tid).order_by(CashEvent.id)
    )).all()
    overrides = (await session.scalars(
        select(CostBasisOverride).where(CostBasisOverride.tenant_id == tid)
        .order_by(CostBasisOverride.id)
    )).all()
    movs = (await session.scalars(
        select(Import).where(Import.tenant_id == tid, Import.kind == "movimentacao")
    )).all()

    ws = Path(tempfile.mkdtemp(prefix="b3irpf-"))
    try:
        b3run.seed_workspace(ws)
        b3run.write_csv(ws / "data" / "trades.csv", _TRADE_HEADER, [
            [t.date, t.side, t.market, t.maturity, t.broker, t.ticker,
             t.qty, t.price, t.amount, t.source_file, t.signed_qty] for t in trades])
        b3run.write_csv(ws / "data" / "cash_events.csv", _EVENT_HEADER, [
            [e.date, e.ticker, e.event_type, e.event_category, e.is_amortization,
             e.qty, e.price, e.amount, e.broker, e.source_file] for e in events])
        b3run.write_csv(ws / "data" / "cost_basis_overrides.csv", _OVERRIDE_HEADER, [
            [o.date, o.ticker, o.kind, o.qty, o.price, o.amount, o.note] for o in overrides])
        for imp in movs:
            if Path(imp.stored_path).exists():
                shutil.copy(imp.stored_path, ws / "b3_imports" / imp.filename)

        proc = await asyncio.to_thread(
            b3run.run_module, ws, ["scripts.irpf_2026", "--year", str(body.year)]
        )
        if proc.returncode != 0:
            raise HTTPException(500, f"irpf_2026 failed: {(proc.stderr or proc.stdout)[-2000:]}")
        return {"year": body.year, "markdown": proc.stdout}
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Strategy gallery (public) ─────────────────────────────────────────────────
# Registry discovery only runs inside the engine venv, so shell into it once and
# cache the JSON in-process. ponytail: process-lifetime cache, no TTL — the
# gallery only changes when the image (its specs/plugins) is rebuilt.
_STRATEGIES_CACHE: list | None = None


@strategies_router.get("")
async def list_strategies() -> list[dict]:
    global _STRATEGIES_CACHE
    if _STRATEGIES_CACHE is None:
        proc = await asyncio.to_thread(enginerun.run_script, "list_strategies.py", [])
        if proc.returncode != 0:
            raise HTTPException(500, f"list_strategies failed: {(proc.stderr or proc.stdout)[-2000:]}")
        _STRATEGIES_CACHE = json.loads(proc.stdout)
    return _STRATEGIES_CACHE


# ── Backtests ─────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str
    params: dict | None = None
    start: str | None = None
    end: str | None = None
    freq: str | None = None


def _run_dict(r: BacktestRun) -> dict:
    return {
        "id": str(r.id), "strategy": r.strategy, "status": r.status,
        "error": r.error, "params": r.params, "metrics": r.metrics,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


@backtests_router.post("")
async def create_backtest(
    body: BacktestRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    known = {s["name"] for s in await list_strategies()}
    if body.strategy not in known:
        raise HTTPException(404, f"unknown strategy: {body.strategy}")
    run = BacktestRun(
        tenant_id=tid, strategy=body.strategy, status="pending",
        params={"params": body.params or {}, "start": body.start,
                "end": body.end, "freq": body.freq},
    )
    session.add(run)
    await session.commit()

    # ponytail: per-request procrastinate pool — mirrors the imports path.
    async with tasks.app.open_async():
        await tasks.run_backtest.defer_async(run_id=str(run.id))
    return _run_dict(run)


@backtests_router.get("")
async def list_backtests(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (
        await session.scalars(
            select(BacktestRun).where(BacktestRun.tenant_id == tid)
            .order_by(BacktestRun.created_at.desc())
        )
    ).all()
    return [_run_dict(r) for r in rows]


async def _get_run(rid: str, tid: uuid.UUID, session: AsyncSession) -> BacktestRun:
    try:
        run = await session.get(BacktestRun, uuid.UUID(rid))
    except ValueError:
        run = None
    if run is None or run.tenant_id != tid:
        raise HTTPException(404, "backtest not found")
    return run


@backtests_router.get("/{run_id}")
async def get_backtest(
    run_id: str,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return _run_dict(await _get_run(run_id, tid, session))


@backtests_router.get("/{run_id}/curves")
async def get_backtest_curves(
    run_id: str,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    run = await _get_run(run_id, tid, session)
    if not run.artifacts_path:
        raise HTTPException(409, f"backtest not finished (status={run.status})")
    path = Path(run.artifacts_path) / "curves.csv"
    if not path.exists():
        raise HTTPException(404, "curves artifact missing")
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    # Column-oriented series: {"date": [...], "aftertax": [...], ...}. dates stay
    # strings; the rest parse to float.
    cols = rows[0].keys() if rows else []
    series: dict[str, list] = {c: [] for c in cols}
    for r in rows:
        for c in cols:
            series[c].append(r[c] if c == "date" else float(r[c]))
    return {"id": run_id, "series": series}


# ── Market status ─────────────────────────────────────────────────────────────

@market_router.get("/status")
async def market_status() -> dict:
    """Health of the engine market plane (plain SQL over ENGINE_DATABASE_URL).
    0-row tables are reported honestly (fresh: null)."""
    conn = await asyncpg.connect(settings.engine_database_url)
    try:
        prices = await conn.fetchrow(
            "SELECT count(*) AS rows, count(DISTINCT ticker) AS tickers, "
            "min(date) AS min_date, max(date) AS max_date FROM prices"
        )
        tables = {}
        for name, datecol in (("corporate_actions", "event_date"),
                              ("fundamentals_pit", "filing_date")):
            row = await conn.fetchrow(
                f"SELECT count(*) AS rows, max({datecol}) AS fresh FROM {name}"
            )
            tables[name] = {
                "rows": row["rows"],
                "fresh": row["fresh"].isoformat() if row["fresh"] else None,
            }
    finally:
        await conn.close()
    return {
        "prices": {
            "rows": prices["rows"],
            "tickers": prices["tickers"],
            "min_date": prices["min_date"].isoformat() if prices["min_date"] else None,
            "max_date": prices["max_date"].isoformat() if prices["max_date"] else None,
        },
        "tables": tables,
    }
