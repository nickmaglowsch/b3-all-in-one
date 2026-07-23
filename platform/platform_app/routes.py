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
from datetime import date
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import b3run, enginerun, tasks
from platform_app.config import settings
from platform_app.db import get_async_session
from platform_app.models import (
    BacktestRun, CashEvent, CostBasisOverride, Import, PortfolioSleeve, Position, Trade,
)
from platform_app.tenancy import get_current_tenant

imports_router = APIRouter(prefix="/imports", tags=["imports"])
portfolio_router = APIRouter(prefix="/portfolio", tags=["portfolio"])
irpf_router = APIRouter(prefix="/irpf", tags=["irpf"])
tlh_router = APIRouter(prefix="/tlh", tags=["tlh"])
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


# ── Portfolio sleeves (spec/05 Feature 3) ─────────────────────────────────────

class SleeveIn(BaseModel):
    strategy_ref: str
    target_pct: float


@portfolio_router.get("/sleeves")
async def get_sleeves(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (await session.scalars(
        select(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid)
        .order_by(PortfolioSleeve.strategy_ref)
    )).all()
    return [{"strategy_ref": s.strategy_ref, "target_pct": float(s.target_pct)} for s in rows]


@portfolio_router.put("/sleeves")
async def put_sleeves(
    body: list[SleeveIn],
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """Replace the whole sleeve set atomically. Validates percentages sum to 100
    (±0.01) and every strategy_ref is 'CASH_CDI' or a real gallery spec."""
    _user, tid = tenant
    refs = [s.strategy_ref for s in body]
    if len(set(refs)) != len(refs):
        raise HTTPException(422, "duplicate strategy_ref in sleeve set")
    total = sum(s.target_pct for s in body)
    if abs(total - 100.0) > 0.01:
        raise HTTPException(422, f"sleeve percentages must sum to 100 (got {total:g})")
    # CASH_CDI is the risk-free sleeve; anything else must be a real gallery spec.
    known = {s["name"] for s in await list_strategies()} | {"CASH_CDI"}
    for ref in refs:
        if ref not in known:
            raise HTTPException(422, f"unknown strategy_ref: {ref}")

    await session.execute(delete(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid))
    for s in body:
        session.add(PortfolioSleeve(
            tenant_id=tid, strategy_ref=s.strategy_ref, target_pct=s.target_pct))
    await session.commit()
    return [{"strategy_ref": s.strategy_ref, "target_pct": s.target_pct} for s in body]


# ── Combined target + rebalance advisor (spec/05 Features 1 & 3) ──────────────
# advise_weights per strategy sleeve is deterministic for a given day's data, so
# cache the JSON per (strategy, date). ponytail: process-lifetime dict keyed by
# the calendar day — same lifecycle as _STRATEGIES_CACHE.
_SLEEVE_WEIGHTS_CACHE: dict[tuple[str, str], dict] = {}


async def _advise(**kwargs) -> dict:
    try:
        return await asyncio.to_thread(enginerun.advise_weights, **kwargs)
    except RuntimeError as e:
        raise HTTPException(500, f"advise_weights failed: {e}")


async def _combined_target(
    session: AsyncSession, tid: uuid.UUID
) -> tuple[dict[str, float], float]:
    """Blend the tenant's sleeves into (combined_weights, cash_weight_target):
    combined[t] = Σ sleeve_pct/100 × sleeve_weights[t]; CASH_CDI pct becomes the
    cash weight. 409 when no sleeves are configured."""
    sleeves = (await session.scalars(
        select(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid)
    )).all()
    if not sleeves:
        raise HTTPException(409, "no sleeves configured — PUT /portfolio/sleeves first")

    today = date.today().isoformat()
    combined: dict[str, float] = {}
    cash_weight = 0.0
    for s in sleeves:
        pct = float(s.target_pct) / 100.0
        if s.strategy_ref == "CASH_CDI":
            cash_weight += pct
            continue
        key = (s.strategy_ref, today)
        adv = _SLEEVE_WEIGHTS_CACHE.get(key)
        if adv is None:
            adv = await _advise(strategy=s.strategy_ref)
            _SLEEVE_WEIGHTS_CACHE[key] = adv
        for t, w in adv["weights"].items():
            combined[t] = combined.get(t, 0.0) + pct * float(w)
    return combined, cash_weight


async def _positions_json(session: AsyncSession, tid: uuid.UUID) -> dict[str, dict]:
    rows = (await session.scalars(
        select(Position).where(Position.tenant_id == tid).order_by(Position.ticker)
    )).all()
    return {p.ticker: {"qty": float(p.qty or 0.0), "avg_cost": float(p.avg_cost or 0.0)}
            for p in rows if p.ticker and (p.qty or 0.0) != 0}


@portfolio_router.get("/target")
async def get_target(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Combined target weights, current weights, and per-ticker drift. Prices the
    book via one advise_weights --weights valuation pass (same basis as the
    rebalance trades)."""
    _user, tid = tenant
    combined, cash_weight = await _combined_target(session, tid)
    positions = await _positions_json(session, tid)

    priced = await _advise(weights=combined, positions=positions, nav_cash=0.0)
    trades = priced.get("trades") or {}
    nav = float(trades.get("nav", 0.0))
    valued = trades.get("positions_valued") or {}
    current = {t: v / nav for t, v in valued.items()} if nav > 0 else {}

    drift = []
    for t in sorted(set(combined) | set(current)):
        tw = float(combined.get(t, 0.0))
        cw = float(current.get(t, 0.0))
        drift.append({"ticker": t, "target_w": tw, "current_w": cw,
                      "drift_w": tw - cw, "drift_brl": (tw - cw) * nav})

    return {
        "as_of": priced["as_of"],
        "combined_weights": combined,
        "current_weights": current,
        "cash_weight_target": cash_weight,
        "drift": drift,
        "nav": nav,
    }


class RebalanceRequest(BaseModel):
    new_cash: float = 0.0
    exclude: list[str] = []
    pins: list[str] = []


def _freeze_target(
    combined: dict[str, float], cash_weight: float, frozen: set[str],
    valued: dict[str, float], final_nav: float,
) -> dict[str, float]:
    """Hold `frozen` (excluded/pinned) tickers at their current weight — no trade —
    and renormalize the rest of `combined` into the remaining budget
    (1 − cash − Σ frozen). Non-held frozen tickers just drop out (never bought)."""
    frozen_w = {t: valued[t] / final_nav for t in frozen
                if valued.get(t) and final_nav > 0}
    nonfrozen = {t: w for t, w in combined.items() if t not in frozen}
    total_nf = sum(nonfrozen.values())
    budget = max(0.0, 1.0 - cash_weight - sum(frozen_w.values()))
    target = dict(frozen_w)
    if total_nf > 0:
        for t, w in nonfrozen.items():
            target[t] = w / total_nf * budget
    return target


@portfolio_router.post("/rebalance-plans")
async def create_rebalance_plan(
    body: RebalanceRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Advisor v0 (single-shot, this month): combined target → integer trade list →
    tax overlay via the TLH machinery. Advisory only; nothing executes."""
    _user, tid = tenant
    combined, cash_weight = await _combined_target(session, tid)
    positions = await _positions_json(session, tid)
    held = set(positions)
    # v0: pins and excludes collapse to one "freeze" set — both mean "don't trade
    # this ticker, keep its current position" (per-ticker keep-in-place).
    frozen = {t.upper() for t in (body.exclude + body.pins)}

    target = combined
    if frozen:
        # One valuation pass (no new_cash) to learn current values, so frozen holdings
        # can be pinned at their current weight before renormalizing the rest.
        val = await _advise(weights=combined, positions=positions, nav_cash=0.0)
        vtrades = val.get("trades") or {}
        valued = {k.upper(): v for k, v in (vtrades.get("positions_valued") or {}).items()}
        final_nav = float(vtrades.get("nav", 0.0)) + body.new_cash
        target = _freeze_target(combined, cash_weight, frozen, valued, final_nav)

    priced = await _advise(weights=target, positions=positions, nav_cash=body.new_cash)
    trades = priced.get("trades") or {"sells": [], "buys": [], "nav": 0.0}
    # Belt-and-suspenders: frozen tickers never appear in a trade (guards rounding).
    sells = [s for s in trades.get("sells", []) if s["ticker"].upper() not in frozen]
    buys = [b for b in trades.get("buys", []) if b["ticker"].upper() not in frozen]
    nav = float(trades.get("nav", 0.0))

    # Tax overlay: run the plan's sells (only tickers actually held) through tlh_plan.
    tax = await _run_tlh_plan(
        session, tid, [(s["ticker"], s["qty"]) for s in sells if s["ticker"] in held])

    notes: list[str] = []
    acao = (tax.get("buckets") or {}).get("AÇÃO") or {}
    limit = float(tax.get("isencao_limit", 20_000.0))
    if float(acao.get("gross_after", 0.0)) > limit:
        notes.append(
            f"ações gross sales this month would exceed R$20k isenção "
            f"(gross pós-vendas R$ {acao['gross_after']:,.2f} > R$ {limit:,.0f})")

    return {
        "as_of": priced["as_of"],
        "nav": nav,
        "new_cash": body.new_cash,
        "trades": {"sells": sells, "buys": buys},
        "tax": tax,
        "notes": notes,
    }


class IrpfRequest(BaseModel):
    year: int = 2025


# IRPF export headers must mirror the canonical CSVs byte-for-byte so irpf_2026 reads
# them exactly as it reads taxcore/data/*.csv (source_file -> __source_file column).
_TRADE_HEADER = ["date", "side", "market", "maturity", "broker", "ticker",
                 "qty", "price", "amount", "__source_file", "signed_qty"]
_EVENT_HEADER = ["date", "ticker", "event_type", "event_category", "is_amortization",
                 "qty", "price", "amount", "broker", "__source_file"]
_OVERRIDE_HEADER = ["date", "ticker", "kind", "qty", "price", "amount", "note"]
_POSITION_HEADER = ["ticker", "qty", "avg_cost", "total_cost", "market"]


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


# ── Tax-loss harvesting ───────────────────────────────────────────────────────

class TlhSell(BaseModel):
    ticker: str
    qty: float


class TlhRequest(BaseModel):
    sells: list[TlhSell] = []
    exclude: list[str] | None = None


async def _latest_engine_prices(tickers: list[str]) -> list[list]:
    """Latest raw close per ticker from the engine market plane (the same source
    the rebalance trades are priced from). Returns [[papel, cotacao], ...] rows for
    a tlh_plan external_prices snapshot."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return []
    conn = await asyncpg.connect(settings.engine_database_url)
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (ticker) ticker, close FROM prices "
            "WHERE ticker = ANY($1::text[]) AND close IS NOT NULL "
            "ORDER BY ticker, date DESC",
            tickers,
        )
    finally:
        await conn.close()
    return [[r["ticker"], float(r["close"])] for r in rows if r["close"]]


async def _run_tlh_plan(
    session: AsyncSession, tid: uuid.UUID,
    sells: list[tuple[str, float]], exclude: list[str] | None = None,
) -> dict:
    """Run taxcore's tlh_plan --json over the tenant's book with the given sells.
    Shared by POST /tlh/plans and the rebalance advisor's tax overlay."""
    trades = (await session.scalars(
        select(Trade).where(Trade.tenant_id == tid).order_by(Trade.id)
    )).all()
    events = (await session.scalars(
        select(CashEvent).where(CashEvent.tenant_id == tid).order_by(CashEvent.id)
    )).all()
    positions = (await session.scalars(
        select(Position).where(Position.tenant_id == tid).order_by(Position.ticker)
    )).all()

    ws = Path(tempfile.mkdtemp(prefix="b3tlh-"))
    try:
        b3run.seed_workspace(ws)
        # tlh_plan reads: data/trades.csv + data/cash_events.csv (build_timeline),
        # data/positions.csv (canonical holdings), and the latest price snapshot in
        # each of data/{snapshots,fii_snapshots,external_prices} (via _common.price_lookup).
        # It does NOT read cost_basis_overrides. cash_events header must mirror the
        # canonical CSV (source_file -> __source_file) like the IRPF route.
        b3run.write_csv(ws / "data" / "trades.csv", _TRADE_HEADER, [
            [t.date, t.side, t.market, t.maturity, t.broker, t.ticker,
             t.qty, t.price, t.amount, t.source_file, t.signed_qty] for t in trades])
        b3run.write_csv(ws / "data" / "cash_events.csv", _EVENT_HEADER, [
            [e.date, e.ticker, e.event_type, e.event_category, e.is_amortization,
             e.qty, e.price, e.amount, e.broker, e.source_file] for e in events])
        b3run.write_csv(ws / "data" / "positions.csv", _POSITION_HEADER, [
            [p.ticker, p.qty, p.avg_cost, p.total_cost, p.market] for p in positions])
        # Price source = the engine market plane (Postgres), the same prices the
        # rebalance trades come from — works in-image where taxcore/data is excluded.
        # price_lookup reads the latest external_prices CSV (columns papel,cotacao).
        (ws / "data" / "external_prices").mkdir(parents=True, exist_ok=True)
        b3run.write_csv(
            ws / "data" / "external_prices" / f"{date.today().isoformat()}.csv",
            ["papel", "cotacao"],
            await _latest_engine_prices([p.ticker for p in positions]),
        )

        args = ["scripts.tlh_plan", "--json"]
        sells_spec = ",".join(f"{t}:{q}" for t, q in sells)
        if sells_spec:
            args += ["--sells", sells_spec]
        if exclude:
            args += ["--exclude", ",".join(exclude)]

        proc = await asyncio.to_thread(b3run.run_module, ws, args)
        if proc.returncode == 2:  # validation error (bad/oversized sell)
            raise HTTPException(422, (proc.stderr or proc.stdout).strip()[-2000:])
        if proc.returncode != 0:
            raise HTTPException(500, f"tlh_plan failed: {(proc.stderr or proc.stdout)[-2000:]}")
        return _parse_tlh_stdout(proc.stdout)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def _parse_tlh_stdout(stdout: str) -> dict:
    """Parse tlh_plan's pretty-printed JSON, skipping any leading ⚠ warning lines
    it prints when a sold ticker has no cotação in the snapshot."""
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == "{":
            return json.loads("\n".join(lines[i:]))
    return json.loads(stdout)  # pure JSON (no warnings)


@tlh_router.post("/plans")
async def tlh_plan(
    body: TlhRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return await _run_tlh_plan(
        session, tid, [(s.ticker, s.qty) for s in body.sells], exclude=body.exclude)


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
