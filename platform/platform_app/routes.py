"""Tenant-scoped data-plane endpoints: imports, portfolio, IRPF.

Every read is scoped by tenant_id from get_current_tenant — never trust an id in the
request. Routers + task functions only (no service/repository layers).
"""
from __future__ import annotations

import asyncio
import calendar
import csv
import datetime
import io
import json
import math
import shutil
import tempfile
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import b3run, enginerun, specstore, tasks
from platform_app.billing import ENTERPRISE, effective_plan, needs_investidor, needs_trader
from platform_app.config import settings
from platform_app.db import get_async_session, get_engine_pool
from platform_app.models import (
    AssetClassOverride, BacktestRun, CashEvent, CostBasisOverride, DarfPayment,
    Import, IngestRun, Portfolio, PortfolioSleeve, Position, RebalanceSettings,
    TaxSummary, Trade, UserStrategy,
)
from platform_app.routes_portfolio_index import current_rf_marks
from platform_app.tenancy import get_current_tenant, get_optional_tenant

imports_router = APIRouter(prefix="/imports", tags=["imports"])
portfolio_router = APIRouter(prefix="/portfolio", tags=["portfolio"])
irpf_router = APIRouter(prefix="/irpf", tags=["irpf"])
tlh_router = APIRouter(prefix="/tlh", tags=["tlh"])
strategies_router = APIRouter(prefix="/strategies", tags=["backtests"])
backtests_router = APIRouter(prefix="/backtests", tags=["backtests"])
market_router = APIRouter(prefix="/market", tags=["market"])


# Admission control on the upload boundary. `await f.read()` materializes the whole
# body in RAM before anything is written, so without a cap one authenticated POST
# of a 5 GB "xlsx" is an OOM of the api container, and a loop of them fills the
# shared /data/uploads volume. A B3 annual extract is tens of KB; 25 MB x 24 files
# is already far beyond any real year.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_FILES = 24
# ...and on what those bytes expand to. An .xlsx is a zip, and the cap above only
# bounds the *compressed* body: XML compresses ~1000:1, so 25 MB of accepted
# upload is tens of GB of sheet once pandas/openpyxl materialises it, and nothing
# downstream bounds memory (the subprocess timeout bounds wall-clock only). On a
# VPS sharing a host with Postgres, the OOM killer picks the victim, not us.
# A real B3 annual extract expands to ~50 KB, so this is ~1000x headroom.
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024


def _uncompressed_size(data: bytes) -> int:
    """Total uncompressed size an .xlsx declares, or 0 if it is not a readable zip.

    The central directory is trusted deliberately: a zip that *understates* it
    truncates its own read, because ZipExtFile stops decompressing at the
    declared size — so a lying header yields corrupt XML, not an OOM.

    Unreadable → 0 → allowed, because openpyxl reads through this same zipfile
    module: whatever fails to parse here fails to parse there too, and lands on
    the path that already exists for a bad upload (load_b3 errors, the Import row
    goes to status="error"). Rejecting here instead would only move the message.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return sum(i.file_size for i in z.infolist())
    except Exception:  # noqa: BLE001 — malformed zip, see docstring
        return 0


def _detect_kind(name: str) -> str:
    low = name.lower()
    if low.startswith("negociacao-"):
        return "negociacao"
    if low.startswith("movimentacao-"):
        return "movimentacao"
    if low.startswith("relatorio-consolidado-"):
        return "posicao"
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
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"no máximo {MAX_UPLOAD_FILES} arquivos por envio")
    for f in files:
        if not (f.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(400, f"only .xlsx accepted: {f.filename}")

    dest = Path(settings.uploads_dir) / str(tid)
    await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
    created: list[Import] = []
    pending: list[tuple[Path, bytes]] = []
    for f in files:
        # Read one byte past the cap: enough to know it is oversized, without ever
        # holding the oversized body. Rejecting after a full read would be the DoS.
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"arquivo grande demais (limite {MAX_UPLOAD_BYTES // 1024 // 1024} MB): {f.filename}")
        # Checked here, on the loop, and not in the worker: this is the only place
        # a tenant's bytes enter the system, so it is the only place the bound has
        # to hold. Cheap — reads the zip index, never the members.
        if _uncompressed_size(data) > MAX_UNCOMPRESSED_BYTES:
            raise HTTPException(
                413, f"planilha grande demais ao descompactar (limite "
                     f"{MAX_UNCOMPRESSED_BYTES // 1024 // 1024} MB): {f.filename}")
        # Starlette hands back the client's filename verbatim, so `..` in it would
        # write outside the tenant's directory — and LGPD erasure rmtree's exactly
        # that directory, so an escaped extract would outlive the account that owns
        # it. The same name is later joined into the IRPF workspace (irpf_report).
        name = Path(f.filename).name
        path = dest / name
        pending.append((path, data))
        imp = Import(
            tenant_id=tid, filename=name, stored_path=str(path),
            kind=_detect_kind(name), status="pending",
        )
        session.add(imp)
        created.append(imp)

    # One thread hop for the writes rather than a synchronous write_bytes per
    # file on the event loop: the cap here is 24 files of 25 MB, and the API is
    # a single uvicorn process, so writing that inline stops every other request.
    def _persist() -> None:
        for path, data in pending:
            path.write_bytes(data)

    await asyncio.to_thread(_persist)
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
    # Was unbounded: every extract the tenant had ever uploaded, on a page-load
    # surface that also polls while anything is processing.
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (
        await session.scalars(
            select(Import).where(Import.tenant_id == tid)
            # Newest first, so a capped page is the recent history rather than
            # the oldest rows. This used to come back oldest-first with the page
            # calling .reverse() on the full list — capping that would have
            # dropped exactly the imports the user just made.
            # Served by ix_imports_tenant_created scanned backwards.
            .order_by(Import.created_at.desc())
            .limit(limit)
            .offset(offset)
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


def _value_position(p, price: float | None, as_of: date | None = None) -> dict:
    """Position row marked to the latest engine close, or — for renda fixa, which
    no feed can mark — to the last per-unit price B3 itself published for the
    contract. Still unpriced (delisted, ticker absent from the market plane, a
    contract with no posição report) → nulls, so the UI leaves it out of the P/L
    base instead of pretending value == cost.

    `as_of` dates the mark and is set only for renda fixa: month-end when B3's
    report is all there is, the plane's last session once terms accrue it
    forward. Either way a number shown beside live closes has to say when it is
    for — a live close carries no date because it is always today's."""
    cost = float(p.total_cost or 0.0)
    mv = round(float(p.qty or 0.0) * price, 2) if price else None
    return {
        "ticker": p.ticker, "qty": p.qty, "avg_cost": p.avg_cost,
        "total_cost": p.total_cost, "market": p.market,
        "last_price": price,
        "price_date": as_of.isoformat() if as_of else None,
        "market_value": mv,
        "pnl": None if mv is None else round(mv - cost, 2),
        "pnl_pct": None if mv is None or cost <= 0 else (mv - cost) / cost,
    }


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
    try:
        prices = dict(await _latest_engine_prices([p.ticker for p in rows]))
    except Exception:
        # ponytail: market plane down → cost-only view instead of a 500. Add a
        # cache/retry here only if the engine DB turns out to be flaky.
        prices = {}
    # Second-choice mark, consulted only where the plane has nothing: a listed
    # ticker always wins, so an RF ETF that trades on the exchange keeps its close.
    # Accrued past B3's month-end report where the tenant entered terms, by the
    # same function the rentabilidade chart draws with — see current_rf_marks.
    rf = await current_rf_marks(session, tid)
    out = []
    for p in rows:
        price = prices.get(p.ticker)
        if price is not None:
            out.append(_value_position(p, price))
            continue
        mark, as_of = rf.get(p.ticker, (None, None))
        out.append(_value_position(p, mark, as_of))
    return out


def _downsample(values: list[float], target: int = 40) -> list[float]:
    """Every k-th close, first and last always kept. ~126 sessions in 6 months →
    ~32 points, which is all a 72px sparkline can resolve anyway."""
    if len(values) <= target:
        return values
    step = math.ceil(len(values) / target)
    out = values[::step]
    if (len(values) - 1) % step:
        out.append(values[-1])
    return out


@portfolio_router.get("/price-history")
async def get_price_history(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, list[float]]:
    """~6 months of closes per held ticker for the positions sparklines, one
    batched query over the whole book. Unpriced tickers are simply absent."""
    _user, tid = tenant
    tickers = sorted({t for t in (await session.scalars(
        select(Position.ticker).where(Position.tenant_id == tid)
    )).all() if t})
    if not tickers:
        return {}
    try:
        pool = await get_engine_pool()
        rows = await pool.fetch(
            "SELECT ticker, close FROM prices "
            "WHERE ticker = ANY($1::text[]) AND close IS NOT NULL "
            # window anchored to the snapshot's own last date, not today —
            # a stale market plane should still draw a chart.
            "AND date >= (SELECT MAX(date) FROM prices) - INTERVAL '6 months' "
            "ORDER BY ticker, date",
            tickers,
        )
    except Exception:
        # ponytail: market plane down → no sparklines, same fallback as
        # /positions. A decoration isn't worth a 500.
        return {}
    series: dict[str, list[float]] = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(float(r["close"]))
    return {t: _downsample(v) for t, v in series.items()}


# Allow-listed so `sort` can never reach the query as raw SQL. Anything not on
# this map falls back to date, which is also the default.
_TRADE_SORTS = {
    "date": Trade.date,
    "side": Trade.side,
    "market": Trade.market,
    "broker": Trade.broker,
    "ticker": Trade.ticker,
    "qty": Trade.qty,
    "price": Trade.price,
    "amount": Trade.amount,
}


@portfolio_router.get("/trades")
async def get_trades(
    # Bounded by the framework rather than clamped silently: `offset=-1` is a
    # Postgres error (500) and `limit=10000000` serialises the whole book, and
    # both used to get that far. A 422 names the bad parameter instead.
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = "date",
    dir: str = "desc",
    # Server-side because the table is server-paginated: filtering the 50 rows in
    # hand would search page 1 and call it the book. Matches ticker or corretora,
    # which is what "what did I do at XP" and "everything in HGLG11" need.
    q: str | None = Query(None, max_length=32),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    # An unrecognised sort used to fall back to date and return 200, so a typo
    # looked like working code that ignored you. Say which values exist.
    if sort not in _TRADE_SORTS:
        raise HTTPException(
            422, f"sort inválido: {sort}. Use um de {', '.join(_TRADE_SORTS)}."
        )
    if dir not in ("asc", "desc"):
        raise HTTPException(422, f"dir inválido: {dir}. Use asc ou desc.")
    # The filter narrows the count too: "1–50 de 3.117" beside three matching
    # rows would be the pager describing a different result set than the table.
    where = [Trade.tenant_id == tid]
    if q:
        needle = f"%{q.strip()}%"
        where.append(Trade.ticker.ilike(needle) | Trade.broker.ilike(needle))

    total = await session.scalar(
        select(func.count()).select_from(Trade).where(*where)
    )
    # Sorting belongs here, not in the page component: ordering only the 50 rows
    # already fetched answers "largest trade on this page", which is not the
    # question anyone clicking a column header is asking.
    col = _TRADE_SORTS[sort]
    asc = dir == "asc"
    # The id tiebreak follows the sort direction so the default column can be
    # served straight off ix_trades_tenant_date (tenant_id, date DESC, id DESC),
    # scanned forwards or backwards. Hard-coding id DESC would ask for
    # (date ASC, id DESC), which that index cannot produce — so the most obvious
    # second click would fall back to a full sort of the tenant's trades.
    ordering = (col.asc(), Trade.id.asc()) if asc else (col.desc(), Trade.id.desc())
    rows = (
        await session.scalars(
            select(Trade)
            .where(*where)
            .order_by(*ordering)
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
    tenant=Depends(needs_investidor),
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
    tenant=Depends(needs_investidor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Replace the whole sleeve set atomically. Validates percentages sum to 100
    (±0.01) and every strategy_ref is 'CASH_CDI' or a real gallery spec. Saving a
    fundamentals-needing strategy is allowed even when none are ingested yet (data
    may arrive later) — the response just carries a warning."""
    _user, tid = tenant
    refs = [s.strategy_ref for s in body]
    if len(set(refs)) != len(refs):
        raise HTTPException(422, "duplicate strategy_ref in sleeve set")
    total = sum(s.target_pct for s in body)
    if abs(total - 100.0) > 0.01:
        raise HTTPException(422, f"sleeve percentages must sum to 100 (got {total:g})")
    # CASH_CDI is the risk-free sleeve; anything else must be a real gallery spec
    # (built-ins + this tenant's user strategies).
    gallery = {s["name"]: s for s in await _gallery(session, tid)}
    known = set(gallery) | {"CASH_CDI"}
    for ref in refs:
        if ref not in known:
            raise HTTPException(422, f"unknown strategy_ref: {ref}")

    await session.execute(delete(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid))
    for s in body:
        session.add(PortfolioSleeve(
            tenant_id=tid, strategy_ref=s.strategy_ref, target_pct=s.target_pct))
    await session.commit()

    # Soft warning (no hard-refuse): chosen strategy needs fundamentals and none
    # are ingested yet. Only touches the engine DB when it can actually matter.
    warnings: list[str] = []
    needs = [r for r in refs if gallery.get(r, {}).get("needs_fundamentals")]
    if needs and not await _fundamentals_available():
        warnings.append(_needs_funds_msg(needs))

    return {
        "sleeves": [{"strategy_ref": s.strategy_ref, "target_pct": s.target_pct}
                    for s in body],
        "warnings": warnings,
    }


# ── Combined target + rebalance advisor (spec/05 Features 1 & 3) ──────────────
# advise_weights per strategy sleeve is deterministic for a given day's data, so
# cache the JSON per (strategy, date, freq). ponytail: process-lifetime dict keyed
# by the calendar day — same lifecycle as _STRATEGIES_CACHE. USER refs also carry
# (tenant_id, updated_at) so two tenants' same-named specs can't collide and a
# same-day edit busts the entry; built-in refs keep the shared 3-tuple key.
_SLEEVE_WEIGHTS_CACHE: dict[tuple, dict] = {}

# Every key here embeds a date or an updated_at, so entries are never reused past
# the day they were written — without a bound the dict just grows by
# (tenants x strategies) per day until the process restarts. Dropping the whole
# thing on overflow is fine: it is a cache, and the next call refills what it needs.
_CACHE_MAX = 1024


def _bounded(cache: dict) -> dict:
    if len(cache) > _CACHE_MAX:
        cache.clear()
    return cache


def _needs_funds_msg(strategies: list[str]) -> str:
    """pt-BR message naming the strategy/strategies that need fundamentals data."""
    uniq = list(dict.fromkeys(strategies))
    names = ", ".join(f"'{s}'" for s in uniq)
    if len(uniq) == 1:
        return (f"A estratégia {names} precisa de dados fundamentalistas, que "
                f"ainda não foram ingeridos neste ambiente.")
    return (f"As estratégias {names} precisam de dados fundamentalistas, que "
            f"ainda não foram ingeridos neste ambiente.")


async def _fundamentals_available() -> bool:
    """True when fundamentals_pit has any rows in the engine market plane.
    Same DSN/pattern as market_status — the actual-availability check the guards
    key on, so a strategy 'just works' the moment fundamentals are ingested."""
    pool = await get_engine_pool()
    # EXISTS, not count(*): the question is "any rows?", and count scans them all.
    return bool(await pool.fetchval("SELECT EXISTS (SELECT 1 FROM fundamentals_pit)"))


async def _advise(**kwargs) -> dict:
    try:
        return await asyncio.to_thread(enginerun.advise_weights, **kwargs)
    except enginerun.EngineNeedsFundamentals:
        raise  # callers turn this into a clean 422 (never a 500)
    except RuntimeError as e:
        raise HTTPException(500, f"advise_weights failed: {e}")


async def _advise_strategy(strategy: str, freq: str, extra_env: dict | None = None) -> dict:
    """Advise a strategy's target weights at the given rebalance cadence (freq).
    freq='ME' is the default path — identical to today. A non-default cadence is
    threaded as ``--params rebalance_freq`` through the engine script (enginerun's
    advise_weights wrapper exposes no params passthrough); reuses run_script + its
    exit-code-3 / last-JSON parsing, no new engine code. extra_env points the
    registry at the tenant's user specs when the strategy is a user ref."""
    if freq == "ME":
        return await _advise(strategy=strategy, extra_env=extra_env)

    def _run() -> dict:
        proc = enginerun.run_script(
            "advise_weights.py",
            [strategy, "--params", json.dumps({"rebalance_freq": freq})],
            extra_env,
        )
        if proc.returncode == enginerun.NEEDS_FUNDAMENTALS_EXIT:
            raise enginerun._needs_fundamentals(proc.stderr, strategy)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
        return enginerun._last_json(proc.stdout)

    try:
        return await asyncio.to_thread(_run)
    except enginerun.EngineNeedsFundamentals:
        raise
    except RuntimeError as e:
        raise HTTPException(500, f"advise_weights failed: {e}")


# ── Per-tenant portfolio settings (spec/05 Features 3 & 4) ────────────────────
# Read-only accessors for the settings owned by routes_portfolio_settings.py.

async def _cash_cdi_tickers(session: AsyncSession, tid: uuid.UUID) -> set[str]:
    """Tickers the tenant marked cash/CDI-equivalent — treated as cash, not equity."""
    rows = (await session.scalars(
        select(AssetClassOverride.ticker).where(
            AssetClassOverride.tenant_id == tid,
            AssetClassOverride.treatment == "cash_cdi",
        )
    )).all()
    return {t.upper() for t in rows}


async def _rebalance_settings(session: AsyncSession, tid: uuid.UUID) -> dict:
    """buy_only_laggards / sell_only_for_harvest / cadence. Absent row → defaults
    that reproduce today's rebalance behavior exactly."""
    row = await session.get(RebalanceSettings, tid)
    if row is None:
        return {"buy_only_laggards": False, "sell_only_for_harvest": False, "cadence": "ME"}
    return {"buy_only_laggards": bool(row.buy_only_laggards),
            "sell_only_for_harvest": bool(row.sell_only_for_harvest),
            "cadence": row.cadence}


async def _combined_target(
    session: AsyncSession, tid: uuid.UUID, freq: str = "ME"
) -> tuple[dict[str, float], float]:
    """Blend the tenant's sleeves into (combined_weights, cash_weight_target):
    combined[t] = Σ sleeve_pct/100 × sleeve_weights[t]; CASH_CDI pct becomes the
    cash weight. 409 when no sleeves are configured. `freq` is the rebalance
    cadence passed to each strategy sleeve (default 'ME' = today's behavior)."""
    sleeves = (await session.scalars(
        select(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid)
    )).all()
    if not sleeves:
        raise HTTPException(409, "no sleeves configured — PUT /portfolio/sleeves first")

    # A sleeve ref may be one of the tenant's user strategies — materialize them
    # and point the engine subprocess at the dir. updated_at keys the cache so a
    # same-day edit isn't served stale and two tenants never collide.
    user_rows = (await session.scalars(
        select(UserStrategy).where(UserStrategy.tenant_id == tid)
    )).all()
    extra_env = (
        {"ENGINE_USER_SPECS_DIR": str(specstore.materialize_user_specs(
            [(str(r.id), r.spec) for r in user_rows], tid))}
        if user_rows else {}
    )
    user_upd = {r.name: r.updated_at.isoformat() if r.updated_at else "" for r in user_rows}

    today = date.today().isoformat()
    combined: dict[str, float] = {}
    cash_weight = 0.0

    def _key(ref: str) -> tuple:
        if ref in user_upd:
            return (ref, today, freq, str(tid), user_upd[ref])
        return (ref, today, freq)

    # Split cash out first: CASH_CDI is the risk-free sleeve and has nothing to
    # advise, so it must never reach the engine.
    equity: list[tuple] = []       # (sleeve_ref, pct, cache_key)
    for s in sleeves:
        pct = float(s.target_pct) / 100.0
        if s.strategy_ref == "CASH_CDI":
            cash_weight += pct
        else:
            equity.append((s.strategy_ref, pct, _key(s.strategy_ref)))

    # Each sleeve is a separate engine subprocess that builds its own market
    # plane, and they are independent — one sleeve's weights depend only on its
    # own strategy. Awaiting them in a loop made a cold three-sleeve portfolio
    # pay three full plane builds end to end. Gathered, the wall clock is the
    # slowest sleeve. Cache hits are resolved before dispatch so a warm sleeve
    # costs nothing.
    todo = [(ref, key) for ref, _pct, key in equity if key not in _SLEEVE_WEIGHTS_CACHE]
    if todo:
        results = await asyncio.gather(
            *(_advise_strategy(ref, freq, extra_env or None) for ref, _key_ in todo),
            return_exceptions=True,
        )
        for (ref, key), res in zip(todo, results):
            if isinstance(res, BaseException):
                continue          # classified below, so one failure is not fatal here
            _bounded(_SLEEVE_WEIGHTS_CACHE)[key] = res
        # return_exceptions keeps a needs-fundamentals sleeve from cancelling the
        # others mid-flight; anything that is not that is still a real failure.
        failures = {ref: res for (ref, _k), res in zip(todo, results)
                    if isinstance(res, BaseException)}
        for ref, exc in failures.items():
            if not isinstance(exc, enginerun.EngineNeedsFundamentals):
                raise exc
    else:
        failures = {}

    missing: list[str] = []   # strategies that need fundamentals not yet ingested
    for ref, pct, key in equity:
        adv = _SLEEVE_WEIGHTS_CACHE.get(key)
        if adv is None:
            exc = failures.get(ref)
            missing.append(getattr(exc, "strategy", None) or ref)
            continue
        for t, w in adv["weights"].items():
            combined[t] = combined.get(t, 0.0) + pct * float(w)
    if missing:
        raise HTTPException(422, _needs_funds_msg(missing))
    return combined, cash_weight


async def _positions_json(session: AsyncSession, tid: uuid.UUID) -> dict[str, dict]:
    rows = (await session.scalars(
        select(Position).where(Position.tenant_id == tid).order_by(Position.ticker)
    )).all()
    return {p.ticker: {"qty": float(p.qty or 0.0), "avg_cost": float(p.avg_cost or 0.0)}
            for p in rows if p.ticker and (p.qty or 0.0) != 0}


@portfolio_router.get("/target")
async def get_target(
    tenant=Depends(needs_investidor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Combined target weights, current weights, and per-ticker drift. Prices the
    book via one advise_weights --weights valuation pass (same basis as the
    rebalance trades)."""
    _user, tid = tenant
    combined, cash_weight = await _combined_target(session, tid)
    positions = await _positions_json(session, tid)
    cash_cdi = await _cash_cdi_tickers(session, tid)

    priced = await _advise(weights=combined, positions=positions, nav_cash=0.0)
    trades = priced.get("trades") or {}
    nav = float(trades.get("nav", 0.0))
    valued = trades.get("positions_valued") or {}
    # Feature 3: a cash/CDI-marked holding counts as cash — drop it from the equity
    # current weights/drift so it isn't shown as a big "sell everything" row; its
    # value stays in NAV, so it surfaces in the residual cash weight below.
    current = {t: v / nav for t, v in valued.items()
               if t.upper() not in cash_cdi} if nav > 0 else {}

    drift = []
    for t in sorted(set(combined) | set(current)):
        tw = float(combined.get(t, 0.0))
        cw = float(current.get(t, 0.0))
        drift.append({"ticker": t, "target_w": tw, "current_w": cw,
                      "drift_w": tw - cw, "drift_brl": (tw - cw) * nav})

    # Residual (non-equity) weight = actual uninvested cash + any cash/CDI holdings.
    cash_weight_current = max(0.0, 1.0 - sum(current.values())) if nav > 0 else 0.0

    return {
        "as_of": priced["as_of"],
        "combined_weights": combined,
        "current_weights": current,
        "cash_weight_target": cash_weight,
        "cash_weight_current": cash_weight_current,
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


def _cap_buys(buys: list[dict], budget: float) -> list[dict]:
    """Greedy top-up: deploy `budget` BRL into buys, biggest gap first, trimming the
    last one to whole shares. Used by buy_only_laggards so the plan only spends the
    cash actually available (new_cash + harvest proceeds), never phantom trim proceeds."""
    kept: list[dict] = []
    for b in sorted(buys, key=lambda x: -float(x.get("est_brl", 0.0))):
        if budget <= 0:
            break
        brl = float(b.get("est_brl", 0.0))
        price = float(b.get("est_price", 0.0))
        if brl <= budget:
            kept.append(b)
            budget -= brl
        elif price > 0:
            qty = math.floor(budget / price)
            if qty > 0:
                kept.append({**b, "qty": qty, "est_brl": qty * price})
                budget -= qty * price
    return kept


@portfolio_router.post("/rebalance-plans")
async def create_rebalance_plan(
    body: RebalanceRequest,
    tenant=Depends(needs_investidor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Advisor v0 (single-shot, this month): combined target → integer trade list →
    tax overlay via the TLH machinery. Advisory only; nothing executes.

    Per-tenant settings (spec/05 Features 3 & 4; defaults = today's behavior):
      · cash/CDI holdings are pinned as cash (not sold, not an equity trim);
      · buy_only_laggards → no trim sells, deploy cash only into underweight names;
      · sell_only_for_harvest → keep only sells that realize a loss;
      · cadence → the rebalance freq passed to the combined target."""
    _user, tid = tenant
    settings_ = await _rebalance_settings(session, tid)
    cadence = settings_["cadence"]
    buy_only = settings_["buy_only_laggards"]
    harvest_only = settings_["sell_only_for_harvest"]

    combined, cash_weight = await _combined_target(session, tid, freq=cadence)
    positions = await _positions_json(session, tid)
    held = set(positions)
    cash_cdi = await _cash_cdi_tickers(session, tid)
    cash_cdi_held = {t for t in held if t.upper() in cash_cdi}
    # v0: pins and excludes collapse to one "freeze" set — both mean "don't trade
    # this ticker, keep its current position" (per-ticker keep-in-place).
    frozen = {t.upper() for t in (body.exclude + body.pins)}

    target = combined
    if frozen or cash_cdi_held:
        # One valuation pass (no new_cash) to learn current values, so frozen and
        # cash/CDI holdings can be pinned at their current weight.
        val = await _advise(weights=combined, positions=positions, nav_cash=0.0)
        vtrades = val.get("trades") or {}
        valued = {k.upper(): v for k, v in (vtrades.get("positions_valued") or {}).items()}
        final_nav = float(vtrades.get("nav", 0.0)) + body.new_cash
        target = _freeze_target(combined, cash_weight, frozen, valued, final_nav) \
            if frozen else dict(combined)
        # Feature 3: pin cash/CDI holdings at their current weight. Unlike a pinned
        # equity, their weight comes out of the cash sleeve (equity budget stays put),
        # so they fulfil the CASH_CDI target instead of being sold as an equity trim.
        for t in cash_cdi_held:
            tu = t.upper()
            w = valued.get(tu, 0.0) / final_nav if final_nav > 0 else 0.0
            if w > 0:
                target[tu] = w

    priced = await _advise(weights=target, positions=positions, nav_cash=body.new_cash)
    trades = priced.get("trades") or {"sells": [], "buys": [], "nav": 0.0}
    # Belt-and-suspenders: frozen / cash-CDI tickers never appear in a trade.
    skip = frozen | {t.upper() for t in cash_cdi_held}
    sells = [s for s in trades.get("sells", []) if s["ticker"].upper() not in skip]
    buys = [b for b in trades.get("buys", []) if b["ticker"].upper() not in skip]
    nav = float(trades.get("nav", 0.0))

    # Feature 4 — sell behavior. Loss determination is preço-médio (est_price vs avg_cost),
    # the same basis the TLH overlay uses; the overlay then runs on the resulting sells.
    avg_cost = {t.upper(): float(p["avg_cost"]) for t, p in positions.items()}
    if harvest_only:
        # Never trim a gain: keep only sells that realize a tax-deductible loss.
        sells = [s for s in sells
                 if float(s.get("est_price", 0.0)) < avg_cost.get(s["ticker"].upper(), 0.0)]
    elif buy_only:
        sells = []  # pure top-up: no trims (loss harvest lives under sell_only_for_harvest)

    # Feature 4 — buy behavior. buy_only_laggards deploys only new_cash + surviving
    # sell proceeds into the underweight names advise already picked.
    if buy_only:
        budget = float(body.new_cash) + sum(float(s.get("est_brl", 0.0)) for s in sells)
        buys = _cap_buys(buys, budget)

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
        "cadence": cadence,
        "settings": settings_,
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


async def _book_csv_rows(
    session: AsyncSession, tid: uuid.UUID
) -> tuple[list[list], list[list]]:
    """``(trade_rows, event_rows)`` for a taxcore workspace, materialised here.

    Plain attribute reads off already-loaded ORM instances (microseconds), done
    on the loop so the worker thread that writes the CSVs never touches a
    Session. Shared by the IRPF report and the DARF apuração so the two can
    never write differently-shaped books for the same script to read.
    """
    trades = (await session.scalars(
        select(Trade).where(Trade.tenant_id == tid).order_by(Trade.id)
    )).all()
    events = (await session.scalars(
        select(CashEvent).where(CashEvent.tenant_id == tid).order_by(CashEvent.id)
    )).all()
    return (
        [[t.date, t.side, t.market, t.maturity, t.broker, t.ticker,
          t.qty, t.price, t.amount, t.source_file, t.signed_qty] for t in trades],
        [[e.date, e.ticker, e.event_type, e.event_category, e.is_amortization,
          e.qty, e.price, e.amount, e.broker, e.source_file] for e in events],
    )


@irpf_router.post("/reports")
async def irpf_report(
    body: IrpfRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    trade_rows, event_rows = await _book_csv_rows(session, tid)
    overrides = (await session.scalars(
        select(CostBasisOverride).where(CostBasisOverride.tenant_id == tid)
        .order_by(CostBasisOverride.id)
    )).all()
    movs = (await session.scalars(
        select(Import).where(Import.tenant_id == tid, Import.kind == "movimentacao")
    )).all()

    override_rows = [
        [o.date, o.ticker, o.kind, o.qty, o.price, o.amount, o.note] for o in overrides]
    extracts = [(imp.stored_path, imp.filename) for imp in movs]

    def _work():
        """Workspace build + subprocess + teardown, off the event loop.

        Serialising a full trade history and copying every uploaded extract used
        to run inline. The API is one uvicorn process with no --workers, so that
        stalled every concurrent request for the duration.
        """
        ws = Path(tempfile.mkdtemp(prefix="b3irpf-", dir=b3run.workspace_root(tid)))
        try:
            b3run.seed_workspace(ws)
            b3run.write_csv(ws / "data" / "trades.csv", _TRADE_HEADER, trade_rows)
            b3run.write_csv(ws / "data" / "cash_events.csv", _EVENT_HEADER, event_rows)
            b3run.write_csv(
                ws / "data" / "cost_basis_overrides.csv", _OVERRIDE_HEADER, override_rows)
            for stored_path, filename in extracts:
                if Path(stored_path).exists():
                    shutil.copy(stored_path, ws / "b3_imports" / filename)
            return b3run.run_module(ws, ["scripts.irpf_2026", "--year", str(body.year)])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    proc = await asyncio.to_thread(_work)
    if proc.returncode != 0:
        raise HTTPException(500, f"irpf_2026 failed: {(proc.stderr or proc.stdout)[-2000:]}")
    return {"year": body.year, "markdown": proc.stdout}


# ── DARF ledger ───────────────────────────────────────────────────────────────
# spec/03: "port of darf_payments.csv as CRUD + monthly apuração view; alert when
# a month's computed tax is due and unrecorded."
#
# Half computed, half stored, and deliberately so. The apuração — gross,
# resultado, compensação, base, IR — is recomputed from the trade history on
# every read by the same taxcore function that renders the report's Renda
# Variável tables, so the ledger can never bill a number the report does not
# show. Only the payment is a row here: what the tenant knows and the engine
# cannot.

# Mirrors taxcore's irpf_2026.CLASSES_APURACAO. Not imported — taxcore runs in
# its own interpreter as a subprocess, which is the whole point of b3run — so the
# guard here is the test that reads a real apuração back and finds these names.
DARF_CLASSES = ("AÇÃO", "FII", "BDR_ETF")
# Ganhos líquidos em renda variável, pessoa física. One code for every class:
# the rate differs, the DARF's código does not.
DARF_CODIGO = "6015"


def _darf_vencimento(apuracao: datetime.date) -> datetime.date:
    """Último dia útil do mês seguinte — when that month's DARF is due.

    ponytail: weekdays only, no feriado calendar. A national holiday landing on
    the last business day pulls the real deadline one day earlier, so this can
    only ever call something "atrasado" later than the Receita would — never
    earlier, which is the direction that would cry wolf.
    """
    if apuracao.month == 12:
        year, month = apuracao.year + 1, 1
    else:
        year, month = apuracao.year, apuracao.month + 1
    due = datetime.date(year, month, calendar.monthrange(year, month)[1])
    while due.weekday() >= 5:  # Saturday, Sunday
        due -= datetime.timedelta(days=1)
    return due


async def _darf_payments(
    session: AsyncSession, tid: uuid.UUID, year: int
) -> list[DarfPayment]:
    return list(await session.scalars(
        select(DarfPayment).where(
            DarfPayment.tenant_id == tid,
            DarfPayment.apuracao >= datetime.date(year, 1, 1),
            DarfPayment.apuracao <= datetime.date(year, 12, 1),
        ).order_by(DarfPayment.apuracao, DarfPayment.classe)
    ))


def _payment_dict(p: DarfPayment | None) -> dict | None:
    if p is None:
        return None
    return {
        "id": p.id,
        "apuracao": p.apuracao.strftime("%Y-%m"),
        "classe": p.classe,
        "valor_principal": float(p.valor_principal),
        "data_pagamento": p.data_pagamento.isoformat() if p.data_pagamento else None,
        "obs": p.obs,
    }


async def _run_apuracao(
    session: AsyncSession, tid: uuid.UUID, year: int
) -> list[dict]:
    """taxcore's per-month Renda Variável apuração for the year, as data.

    Far cheaper than POST /irpf/reports even though it is the same script:
    ``--json`` returns before Bens e Direitos and the renda fixa sections, which
    are what parse every uploaded Excel file. This needs only trades and
    cash_events, so the workspace copies no extracts.
    """
    trade_rows, event_rows = await _book_csv_rows(session, tid)
    if not trade_rows:
        # build_timeline over an empty book has no columns to sort. An empty
        # ledger is the honest answer anyway — nothing was sold, nothing is due.
        return []

    def _work():
        ws = Path(tempfile.mkdtemp(prefix="b3darf-", dir=b3run.workspace_root(tid)))
        try:
            b3run.seed_workspace(ws)
            b3run.write_csv(ws / "data" / "trades.csv", _TRADE_HEADER, trade_rows)
            b3run.write_csv(ws / "data" / "cash_events.csv", _EVENT_HEADER, event_rows)
            return b3run.run_module(
                ws, ["scripts.irpf_2026", "--year", str(year), "--json"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    proc = await asyncio.to_thread(_work)
    if proc.returncode != 0:
        raise HTTPException(
            500, f"irpf_2026 --json failed: {(proc.stderr or proc.stdout)[-2000:]}")
    return b3run.last_json(proc.stdout).get("apuracao") or []


@irpf_router.get("/darf")
async def darf_ledger(
    year: int | None = Query(None, ge=2000, le=2100),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """The year's DARFs: what the engine says is owed, what the tenant recorded.

    Only months that owe something — or that already carry a recorded payment —
    are listed. The full twelve-month grid, including the quiet months, is the
    IRPF report's job; here a zero row would be one nobody can act on and one
    that reads like an unpaid one.
    """
    _user, tid = tenant
    year = year or date.today().year
    payments = {(p.apuracao, p.classe): p for p in await _darf_payments(session, tid, year)}
    today = date.today()

    meses = []
    for r in await _run_apuracao(session, tid, year):
        apuracao = datetime.date(year, r["month"], 1)
        pago = payments.get((apuracao, r["classe"]))
        ir = round(r["ir"], 2)
        if ir <= 0 and pago is None:
            continue
        vencimento = _darf_vencimento(apuracao)
        meses.append({
            "apuracao": apuracao.strftime("%Y-%m"),
            "classe": r["classe"],
            "codigo": DARF_CODIGO,
            "gross": r["gross"],
            "pnl": r["pnl"],
            "compensacao": r["compensacao"],
            "base": r["base"],
            "aliquota": r["aliquota"],
            "ir_devido": ir,
            "vencimento": vencimento.isoformat(),
            "pagamento": _payment_dict(pago),
            # "atrasado" is the alert: computed tax, past its due date, and
            # nothing recorded against it.
            "status": "pago" if pago else ("atrasado" if today > vencimento else "pendente"),
        })
    meses.sort(key=lambda m: (m["apuracao"], m["classe"]))

    return {
        "year": year,
        "codigo": DARF_CODIGO,
        "meses": meses,
        "total_devido": round(sum(m["ir_devido"] for m in meses), 2),
        "total_pago": round(sum(m["pagamento"]["valor_principal"]
                                for m in meses if m["pagamento"]), 2),
        "atrasados": sum(1 for m in meses if m["status"] == "atrasado"),
    }


class DarfPaymentIn(BaseModel):
    apuracao: str  # "AAAA-MM"
    classe: str
    valor_principal: float
    data_pagamento: datetime.date | None = None
    obs: str | None = None


@irpf_router.put("/darf")
async def put_darf_payments(
    body: list[DarfPaymentIn],
    year: int | None = Query(None, ge=2000, le=2100),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """Replace the year's recorded DARFs atomically — the same shape as the
    portfolio settings PUTs.

    ponytail: delete-and-reinsert, not per-row CRUD. A year holds a handful of
    rows, nothing references their ids, and the scoping is what keeps one year's
    edit from touching another's.
    """
    _user, tid = tenant
    year = year or date.today().year

    seen: dict[tuple[datetime.date, str], DarfPaymentIn] = {}
    for p in body:
        try:
            apuracao = datetime.datetime.strptime(p.apuracao, "%Y-%m").date()
        except ValueError:
            raise HTTPException(422, f"apuração inválida: {p.apuracao!r} (esperado AAAA-MM)")
        if apuracao.year != year:
            raise HTTPException(422, f"apuração {p.apuracao} fora do ano {year}")
        classe = p.classe.strip().upper()
        if classe not in DARF_CLASSES:
            raise HTTPException(422, f"classe desconhecida: {p.classe!r}")
        if p.valor_principal < 0:
            raise HTTPException(422, f"valor negativo em {p.apuracao} {classe}")
        if (apuracao, classe) in seen:
            raise HTTPException(422, f"apuração {p.apuracao} {classe} repetida")
        seen[(apuracao, classe)] = p

    await session.execute(delete(DarfPayment).where(
        DarfPayment.tenant_id == tid,
        DarfPayment.apuracao >= datetime.date(year, 1, 1),
        DarfPayment.apuracao <= datetime.date(year, 12, 1),
    ))
    for (apuracao, classe), p in sorted(seen.items()):
        session.add(DarfPayment(
            tenant_id=tid, apuracao=apuracao, classe=classe,
            valor_principal=round(p.valor_principal, 2),
            data_pagamento=p.data_pagamento,
            obs=(p.obs or "").strip() or None,
        ))
    await session.commit()
    return [_payment_dict(r) for r in await _darf_payments(session, tid, year)]


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
    pool = await get_engine_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT ON (ticker) ticker, close FROM prices "
        "WHERE ticker = ANY($1::text[]) AND close IS NOT NULL "
        "ORDER BY ticker, date DESC",
        tickers,
    )
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

    # Materialise every row payload here, on the loop: these are plain attribute
    # reads off already-loaded ORM instances (microseconds), and doing them now
    # means the worker thread below never touches a Session.
    trade_rows = [
        [t.date, t.side, t.market, t.maturity, t.broker, t.ticker,
         t.qty, t.price, t.amount, t.source_file, t.signed_qty] for t in trades]
    event_rows = [
        [e.date, e.ticker, e.event_type, e.event_category, e.is_amortization,
         e.qty, e.price, e.amount, e.broker, e.source_file] for e in events]
    position_rows = [
        [p.ticker, p.qty, p.avg_cost, p.total_cost, p.market] for p in positions]
    # asyncpg, so it has to run on the loop rather than inside the thread.
    price_rows = await _latest_engine_prices([p.ticker for p in positions])

    args = ["scripts.tlh_plan", "--json"]
    sells_spec = ",".join(f"{t}:{q}" for t, q in sells)
    if sells_spec:
        args += ["--sells", sells_spec]
    if exclude:
        args += ["--exclude", ",".join(exclude)]

    def _work():
        """Everything that touches the filesystem, in one thread hop.

        Two copytrees, three CSV serialisations of the tenant's whole history, a
        subprocess and an rmtree. Only the subprocess used to be offloaded; the
        rest ran on the event loop, which is the entire API — this is a single
        uvicorn process with no --workers, so a book being written to disk
        stopped every other request in flight.
        """
        ws = Path(tempfile.mkdtemp(prefix="b3tlh-", dir=b3run.workspace_root(tid)))
        try:
            b3run.seed_workspace(ws)
            # tlh_plan reads: data/trades.csv + data/cash_events.csv (build_timeline),
            # data/positions.csv (canonical holdings), and the latest price snapshot in
            # each of data/{snapshots,fii_snapshots,external_prices} (via _common.price_lookup).
            # It does NOT read cost_basis_overrides. cash_events header must mirror the
            # canonical CSV (source_file -> __source_file) like the IRPF route.
            b3run.write_csv(ws / "data" / "trades.csv", _TRADE_HEADER, trade_rows)
            b3run.write_csv(ws / "data" / "cash_events.csv", _EVENT_HEADER, event_rows)
            b3run.write_csv(ws / "data" / "positions.csv", _POSITION_HEADER, position_rows)
            # Price source = the engine market plane (Postgres), the same prices the
            # rebalance trades come from — works in-image where taxcore/data is excluded.
            # price_lookup reads the latest external_prices CSV (columns papel,cotacao).
            (ws / "data" / "external_prices").mkdir(parents=True, exist_ok=True)
            b3run.write_csv(
                ws / "data" / "external_prices" / f"{date.today().isoformat()}.csv",
                ["papel", "cotacao"], price_rows,
            )
            return b3run.run_module(ws, args)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    proc = await asyncio.to_thread(_work)
    if proc.returncode == 2:  # validation error (bad/oversized sell)
        raise HTTPException(422, (proc.stderr or proc.stdout).strip()[-2000:])
    if proc.returncode != 0:
        raise HTTPException(500, f"tlh_plan failed: {(proc.stderr or proc.stdout)[-2000:]}")
    return _parse_tlh_stdout(proc.stdout)


def _parse_tlh_stdout(stdout: str) -> dict:
    """Parse tlh_plan's pretty-printed JSON, skipping any leading ⚠ warning lines
    it prints when a sold ticker has no cotação in the snapshot."""
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == "{":
            return json.loads("\n".join(lines[i:]))
    return json.loads(stdout)  # pure JSON (no warnings)


# ── Situação fiscal (cached) ─────────────────────────────────────────────────
# tlh_plan with NO sells is, by its own docstring, "the current situation
# (carry/mtd per bucket) with no plan" — isenção headroom, prejuízo a compensar,
# IR devido and harvest candidates, all from the same engine that scores a
# backtest and files the IRPF. That is the whole product thesis in one payload.
#
# It is cached because producing it dumps the tenant's entire trade history to
# CSV and shells into taxcore: seconds. Portfólio reads this on load, so the GET
# never computes — it returns what is stored, says when it was computed, and says
# whether an import has landed since. Recomputing is always an explicit act.


def _owed_today(payload: dict) -> dict:
    """IR actually owed on the month as it stands, per bucket.

    NOT `buckets[cls].ir`. That field means three different things depending on
    which branch of tlh_plan produced it, and in the "TLH recomendado" branch it
    is the IR *after executing a harvest the user has not executed* — zero, very
    often, precisely when the real DARF is largest. Reading it as "devido" prints
    R$ 0,00 to someone who owes money, which is the one thing this product
    promises never to do.

    So the owed-today figure is derived here from `situation`, which is plain
    month-to-date fact: gross sales, realised PnL, and accumulated carry. The
    rules mirror tlh_plan's own no-op branches — ações are isentas at or below
    the R$20k monthly gross (`gross <= ISENCAO_LIMIT`, inclusive), FIIs have no
    isenção, and carry offsets a positive result before the rate applies.
    """
    limit = payload.get("isencao_limit") or 0.0
    rates = payload.get("rates") or {}
    out = {}
    for cls, s in (payload.get("situation") or {}).items():
        gross = s.get("gross_mtd") or 0.0
        pnl = s.get("pnl_mtd") or 0.0
        carry = s.get("carry") or 0.0
        # Inclusive, matching tlh_plan: exactly R$20.000,00 of gross is isento.
        isento = cls == "AÇÃO" and gross <= limit
        carry_used = min(carry, max(0.0, pnl))
        taxable = 0.0 if isento else max(0.0, pnl - carry_used)
        out[cls] = {
            "isento": isento,
            "carry_used": carry_used,
            "taxable": taxable,
            "ir": taxable * (rates.get(cls) or 0.0),
        }
    return out


def _month_key(d: datetime.date) -> str:
    return d.strftime("%m/%Y")


async def _tax_summary_payload(session: AsyncSession, tid: uuid.UUID) -> dict:
    """Read the cached row and report its freshness. Never runs the engine."""
    row = await session.get(TaxSummary, tid)
    if row is None:
        return {"payload": None, "computed_at": None, "stale": False, "stale_reason": None}
    # An extract uploaded after the computation makes the figure wrong, not
    # merely old — including one still processing, which is why this counts every
    # import rather than only the done ones.
    newer = await session.scalar(
        select(func.count()).select_from(Import).where(
            Import.tenant_id == tid, Import.created_at > row.computed_at
        )
    )
    # Every figure in `situation` is month-to-date, so the month rolling over
    # invalidates them with no import involved: the isenção resets, pnl_mtd
    # returns to zero, and a payload from last month would otherwise report
    # itself fresh while saying "neste mês" about a month that ended.
    rolled = (payload := row.payload).get("month") != _month_key(date.today())
    reason = "import" if newer else "month" if rolled else None
    return {
        "payload": payload,
        "computed_at": row.computed_at.isoformat(),
        "stale": bool(newer or rolled),
        "stale_reason": reason,
    }


@portfolio_router.get("/tax-summary")
async def get_tax_summary(
    tenant=Depends(needs_investidor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return await _tax_summary_payload(session, tid)


# One apuração per tenant at a time. Each is a multi-second subprocess that
# dumps the whole book to CSV while holding a pooled connection, and the button
# that fires it sits on a page-load surface — two tabs, or a held-down click,
# is enough to stack them. In-process only: this is a single-API-container
# deployment, and the upsert below is what keeps correctness when it is not.
_tax_refresh_locks: dict[uuid.UUID, asyncio.Lock] = {}


@portfolio_router.post("/tax-summary/refresh")
async def refresh_tax_summary(
    tenant=Depends(needs_investidor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    has_trades = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.tenant_id == tid)
    )
    if not has_trades:
        # tlh_plan over an empty book has nothing to apurar. A 422 keeps this
        # consistent with /portfolio/index, whose 422 the frontend already reads
        # as "no history yet" rather than as a fault.
        raise HTTPException(422, "Sem negociações para apurar.")

    lock = _tax_refresh_locks.setdefault(tid, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "Uma apuração já está em andamento.")
    async with lock:
        # Stamped BEFORE the run, not after. The engine reads the book at T0 and
        # returns seconds later at T1; stamping T1 makes an import that landed in
        # between look older than the computation, so the payload would report
        # itself fresh while missing those trades. Over-reporting stale is the
        # safe direction.
        started = datetime.datetime.now(datetime.timezone.utc)
        payload = await _run_tlh_plan(session, tid, [])
        payload["owed"] = _owed_today(payload)

        # Upsert, not read-modify-write: two refreshes racing on a tenant with no
        # cached row would both see None and both INSERT the same primary key.
        await session.execute(
            pg_insert(TaxSummary)
            .values(tenant_id=tid, payload=payload, computed_at=started)
            .on_conflict_do_update(
                index_elements=[TaxSummary.tenant_id],
                set_={"payload": payload, "computed_at": started},
            )
        )
        await session.commit()
    return await _tax_summary_payload(session, tid)


@tlh_router.post("/plans")
async def tlh_plan(
    body: TlhRequest,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return await _run_tlh_plan(
        session, tid, [(s.ticker, s.qty) for s in body.sells], exclude=body.exclude)


# ── Strategy gallery (built-ins + per-tenant user strategies) ─────────────────
# Registry discovery only runs inside the engine venv, so shell into it and cache
# the JSON in-process. ponytail: process-lifetime caches, no TTL. Built-ins only
# change on an image rebuild; the per-tenant gallery self-heals via a cheap
# (count, max(updated_at)) fingerprint and is popped on save/delete.
_STRATEGIES_CACHE: list | None = None                    # built-ins, global
_TENANT_GALLERY: dict[uuid.UUID, tuple[tuple, list]] = {}  # tid -> (fingerprint, merged)
_SIGNALS_CACHE: list | None = None                       # named-signal catalog, global


async def _builtins() -> list[dict]:
    """The built-in gallery (no ENGINE_USER_SPECS_DIR), tagged source=builtin."""
    global _STRATEGIES_CACHE
    if _STRATEGIES_CACHE is None:
        proc = await asyncio.to_thread(enginerun.run_script, "list_strategies.py", [])
        if proc.returncode != 0:
            raise HTTPException(500, f"list_strategies failed: {(proc.stderr or proc.stdout)[-2000:]}")
        _STRATEGIES_CACHE = [{**s, "source": "builtin"} for s in json.loads(proc.stdout)]
    return _STRATEGIES_CACHE


async def _gallery(session: AsyncSession, tid: uuid.UUID | None) -> list[dict]:
    """Merged strategy gallery. tid=None → built-ins only. Otherwise fingerprint
    the tenant's rows; 0 rows → built-ins; else materialize + shell list_strategies
    with the env var (built-ins + user specs, merged by the registry) and cache
    under the fingerprint. User entries are tagged source=user."""
    if tid is None:
        return await _builtins()
    count, maxupd = (await session.execute(
        select(func.count(UserStrategy.id), func.max(UserStrategy.updated_at))
        .where(UserStrategy.tenant_id == tid)
    )).one()
    if not count:
        return await _builtins()
    fingerprint = (count, maxupd.isoformat() if maxupd else None)
    cached = _TENANT_GALLERY.get(tid)
    if cached and cached[0] == fingerprint:
        return cached[1]

    rows = (await session.scalars(
        select(UserStrategy).where(UserStrategy.tenant_id == tid)
    )).all()
    d = specstore.materialize_user_specs([(str(r.id), r.spec) for r in rows], tid)
    proc = await asyncio.to_thread(
        enginerun.run_script, "list_strategies.py", [], {"ENGINE_USER_SPECS_DIR": str(d)})
    if proc.returncode != 0:
        raise HTTPException(500, f"list_strategies failed: {(proc.stderr or proc.stdout)[-2000:]}")
    user_names = {r.name for r in rows}
    merged = [{**s, "source": "user" if s["name"] in user_names else "builtin"}
              for s in json.loads(proc.stdout)]
    _bounded(_TENANT_GALLERY)[tid] = (fingerprint, merged)
    return merged


@strategies_router.get("")
async def list_strategies(
    tenant=Depends(get_optional_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """Public: anon → built-ins; authed → built-ins + the tenant's user strategies
    (each tagged with a ``source`` of "builtin" or "user")."""
    return await _gallery(session, tenant[1] if tenant else None)


@strategies_router.get("/signals")
async def list_signals() -> list[dict]:
    """Named-signal catalog for the factor picker (shell list_signals.py once)."""
    global _SIGNALS_CACHE
    if _SIGNALS_CACHE is None:
        proc = await asyncio.to_thread(enginerun.run_script, "list_signals.py", [])
        if proc.returncode != 0:
            raise HTTPException(500, f"list_signals failed: {(proc.stderr or proc.stdout)[-2000:]}")
        _SIGNALS_CACHE = json.loads(proc.stdout)
    return _SIGNALS_CACHE


@strategies_router.get("/{name}/spec")
async def get_strategy_spec(
    name: str,
    tenant=Depends(get_optional_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Raw spec of a config (spec-kind) strategy — for cloning a built-in into user
    space (the gallery only exposes params, which loses factor/universe/selection).
    404 if the strategy is code-kind (no data spec) or unknown. Built-ins are global;
    an authed tenant's own spec-kind strategies resolve too (env-var, like _gallery)."""
    extra_env = await specstore.user_specs_env(session, tenant[1]) if tenant else {}
    proc = await asyncio.to_thread(
        enginerun.run_script, "get_strategy_spec.py", ["--name", name], extra_env)
    if proc.returncode != 0:
        raise HTTPException(404, f"strategy {name!r} has no cloneable spec")
    return json.loads(proc.stdout)


# ── User-created strategies (spec/06) ─────────────────────────────────────────
# Pydantic validates the statically-checkable rules at the boundary (name, numeric
# ranges, rebalance enum, portfolio weights). The engine's validate_spec.py does the
# deep signal/factor validation; the gallery resolves sleeve refs + the cycle guard.

_REBALANCE = Literal["ME", "QE", "W-FRI"]


class _RankUniverse(BaseModel):
    min_adtv: float = Field(default=1_000_000.0, ge=0)
    min_price: float = Field(default=1.0, ge=0)
    smallcap_below_median: bool = False


class _RankSignal(BaseModel):
    factor: str | None = None
    expr: str | None = None
    lookback: int | None = Field(default=None, ge=1, le=36)
    shift: int | None = Field(default=None, ge=0)
    range: list[float] | None = None
    apply_glitch: bool | None = None

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.factor) == bool(self.expr):
            raise ValueError("signal needs exactly one of factor / expr")
        return self


class _RankSelection(BaseModel):
    top_pct: float | None = Field(default=None, gt=0, le=1)
    top_n: int | None = Field(default=None, ge=1)
    min_names: int = Field(default=1, ge=1)
    dedup_roots: bool = False

    @model_validator(mode="after")
    def _one_cut(self):
        if (self.top_pct is None) == (self.top_n is None):
            raise ValueError("selection needs exactly one of top_pct / top_n")
        return self


class RankStrategyIn(BaseModel):
    kind: Literal["rank_and_hold"]
    name: str = Field(min_length=1)
    description: str = ""
    rebalance: _REBALANCE = "ME"
    needs_fundamentals: bool = False
    warmup_pad: int | None = Field(default=None, ge=0)
    weighting: Literal["equal", "market_cap"] = "equal"
    universe: _RankUniverse = Field(default_factory=_RankUniverse)
    signal: _RankSignal
    selection: _RankSelection


class PortfolioSleeveIn(BaseModel):
    strategy_ref: str = Field(min_length=1)
    target_pct: float = Field(gt=0, le=100)


class PortfolioStrategyIn(BaseModel):
    kind: Literal["fixed_weight"]
    name: str = Field(min_length=1)
    description: str = ""
    rebalance: _REBALANCE = "QE"
    sleeves: list[PortfolioSleeveIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _weights_sum(self):
        total = sum(s.target_pct for s in self.sleeves)
        if abs(total - 100.0) > 1e-2:  # == weights sum to 1 ± 1e-4
            raise ValueError(f"portfolio percentages must sum to 100 (got {total:g})")
        return self


StrategyIn = Annotated[RankStrategyIn | PortfolioStrategyIn, Field(discriminator="kind")]


def _compile_rank(m: RankStrategyIn) -> dict:
    spec: dict = {
        "name": m.name.strip(), "kind": "rank_and_hold", "description": m.description,
        "rebalance": m.rebalance, "weighting": m.weighting,
        "universe": m.universe.model_dump(exclude_none=True),
        "signal": m.signal.model_dump(exclude_none=True),
        "selection": m.selection.model_dump(exclude_none=True),
    }
    if m.needs_fundamentals:
        spec["needs_fundamentals"] = True
    if m.warmup_pad is not None:
        spec["warmup_pad"] = m.warmup_pad
    return spec


def _compile_portfolio(m: PortfolioStrategyIn) -> dict:
    sleeves = [
        {"asset": "CDI", "weight": s.target_pct / 100.0} if s.strategy_ref == "CASH_CDI"
        else {"strategy": s.strategy_ref, "weight": s.target_pct / 100.0}
        for s in m.sleeves
    ]
    return {
        "name": m.name.strip(), "kind": "fixed_weight", "description": m.description,
        "rebalance": m.rebalance, "sleeves": sleeves,
    }


async def _validate_spec_engine(spec: dict) -> bool:
    """Shell validate_spec.py; raise 422 on structured errors, return
    needs_fundamentals (inferred from f_* refs)."""
    proc = await asyncio.to_thread(
        enginerun.run_script, "validate_spec.py", ["--spec-json", json.dumps(spec)])
    try:
        report = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise HTTPException(500, f"validate_spec failed: {(proc.stderr or proc.stdout)[-2000:]}")
    if not report.get("ok"):
        raise HTTPException(422, "; ".join(report.get("errors") or ["invalid spec"]))
    return bool(report.get("needs_fundamentals"))


async def _compile_and_validate(
    body, session: AsyncSession, tid: uuid.UUID, *, exclude_id: uuid.UUID | None = None
) -> tuple[dict, list[str]]:
    """Pydantic-validated body → engine spec dict + soft warnings. Rejects
    built-in-name collisions, unresolvable sleeve refs, and portfolio cycles."""
    name = body.name.strip()
    builtin_names = {s["name"] for s in await _builtins()}
    if name in builtin_names or name == "CASH_CDI":
        raise HTTPException(422, f"name {name!r} collides with a built-in strategy")

    if body.kind == "rank_and_hold":
        spec = _compile_rank(body)
    else:
        rows = (await session.scalars(
            select(UserStrategy).where(UserStrategy.tenant_id == tid)
        )).all()
        user_fixed = {r.name for r in rows if r.kind == "fixed_weight" and r.id != exclude_id}
        user_rank = {r.name for r in rows if r.kind == "rank_and_hold"}
        resolvable = builtin_names | user_rank | {"CASH_CDI"}
        for s in body.sleeves:
            ref = s.strategy_ref
            if ref in user_fixed or ref == name:
                raise HTTPException(
                    422, f"sleeve {ref!r}: a portfolio cannot reference another user "
                         f"portfolio (would recurse)")
            if ref not in resolvable:
                raise HTTPException(422, f"unknown sleeve strategy_ref: {ref!r}")
        spec = _compile_portfolio(body)

    needs_funds = await _validate_spec_engine(spec)
    if needs_funds:
        spec["needs_fundamentals"] = True  # persist inferred flag for gallery/engine
    warnings: list[str] = []
    if needs_funds and not await _fundamentals_available():
        warnings.append(_needs_funds_msg([name]))
    return spec, warnings


def _user_strategy_dict(r: UserStrategy) -> dict:
    return {
        "id": str(r.id), "name": r.name, "kind": r.kind, "spec": r.spec,
        "source": "user",
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _get_user_strategy(sid: str, tid: uuid.UUID, session: AsyncSession) -> UserStrategy:
    try:
        row = await session.get(UserStrategy, uuid.UUID(sid))
    except ValueError:
        row = None
    if row is None or row.tenant_id != tid:
        raise HTTPException(404, "strategy not found")
    return row


@strategies_router.get("/user")
async def list_user_strategies(
    tenant=Depends(needs_trader),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (await session.scalars(
        select(UserStrategy).where(UserStrategy.tenant_id == tid).order_by(UserStrategy.name)
    )).all()
    return [_user_strategy_dict(r) for r in rows]


@strategies_router.post("/user")
async def create_user_strategy(
    body: StrategyIn = Body(...),
    tenant=Depends(needs_trader),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    spec, warnings = await _compile_and_validate(body, session, tid)
    row = UserStrategy(tenant_id=tid, name=body.name.strip(), kind=body.kind, spec=spec)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"strategy name {body.name.strip()!r} already exists")
    _TENANT_GALLERY.pop(tid, None)
    await session.refresh(row)
    return {**_user_strategy_dict(row), "warnings": warnings}


async def _cascade_rename(
    session: AsyncSession, tid: uuid.UUID, old: str, new: str, exclude_id: uuid.UUID
) -> None:
    """Rename old→new across the tenant's live sleeves and other user portfolios
    that reference it (keeps the registry key consistent)."""
    await session.execute(
        update(PortfolioSleeve)
        .where(PortfolioSleeve.tenant_id == tid, PortfolioSleeve.strategy_ref == old)
        .values(strategy_ref=new)
    )
    others = (await session.scalars(
        select(UserStrategy).where(
            UserStrategy.tenant_id == tid, UserStrategy.kind == "fixed_weight",
            UserStrategy.id != exclude_id,
        )
    )).all()
    for o in others:
        sleeves = o.spec.get("sleeves", [])
        if any(sl.get("strategy") == old for sl in sleeves):
            o.spec = {**o.spec, "sleeves": [
                {**sl, "strategy": new} if sl.get("strategy") == old else sl
                for sl in sleeves
            ]}


@strategies_router.put("/user/{sid}")
async def update_user_strategy(
    sid: str,
    body: StrategyIn = Body(...),
    tenant=Depends(needs_trader),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    row = await _get_user_strategy(sid, tid, session)
    if body.kind != row.kind:
        raise HTTPException(422, "cannot change a strategy's kind — delete and recreate")
    spec, warnings = await _compile_and_validate(body, session, tid, exclude_id=row.id)
    old_name, new_name = row.name, body.name.strip()
    row.name = new_name
    row.spec = spec
    if new_name != old_name:
        await _cascade_rename(session, tid, old_name, new_name, row.id)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"strategy name {new_name!r} already exists")
    _TENANT_GALLERY.pop(tid, None)
    await session.refresh(row)
    return {**_user_strategy_dict(row), "warnings": warnings}


@strategies_router.delete("/user/{sid}")
async def delete_user_strategy(
    sid: str,
    tenant=Depends(needs_trader),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    row = await _get_user_strategy(sid, tid, session)
    n = await session.scalar(
        select(func.count()).select_from(PortfolioSleeve)
        .where(PortfolioSleeve.tenant_id == tid, PortfolioSleeve.strategy_ref == row.name)
    )
    if n:
        raise HTTPException(409, f"{row.name!r} is a live sleeve — remove it from /portfolio/sleeves first")
    others = (await session.scalars(
        select(UserStrategy).where(
            UserStrategy.tenant_id == tid, UserStrategy.kind == "fixed_weight",
            UserStrategy.id != row.id,
        )
    )).all()
    for o in others:
        if any(sl.get("strategy") == row.name for sl in o.spec.get("sleeves", [])):
            raise HTTPException(409, f"{row.name!r} is referenced by portfolio {o.name!r}")
    await session.delete(row)
    await session.commit()
    _TENANT_GALLERY.pop(tid, None)
    return {"deleted": sid}


@strategies_router.post("/user/{sid}/promote")
async def promote_user_strategy(
    sid: str,
    tenant=Depends(needs_trader),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Compile a portfolio strategy's sleeves into the live target (reuses the
    existing PUT /portfolio/sleeves logic — same validation + atomic replace)."""
    _user, tid = tenant
    row = await _get_user_strategy(sid, tid, session)
    if row.kind != "fixed_weight":
        raise HTTPException(422, "only portfolio (fixed_weight) strategies can be promoted")
    sleeves = [
        SleeveIn(
            strategy_ref="CASH_CDI" if sl.get("asset") == "CDI" else sl["strategy"],
            target_pct=round(float(sl["weight"]) * 100.0, 6),
        )
        for sl in row.spec.get("sleeves", [])
    ]
    return await put_sleeves(sleeves, tenant, session)


# ── Backtests ─────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    # start/end/freq are forwarded to the engine, where `freq` reaches
    # pandas .resample() over ~20 years of daily prices. As a free-form string it
    # accepts "s", which asks the shared worker to build a second-resolution frame
    # of hundreds of millions of rows — an OOM of the box the API runs on. Reuse
    # the cadence enum the rest of the app already validates against, and let
    # pydantic parse the dates instead of passing text through.
    strategy: str
    params: dict | None = None
    start: date | None = None
    end: date | None = None
    freq: _REBALANCE | None = None


# Backtests one ACCOUNT may have queued or running at once. The worker runs two
# jobs at a time on a shared VPS (spec/01) and `queueing_lock` only stops one
# *kind* of job overlapping — so without this a single paying customer submitting
# a hundred runs owns both slots for the rest of the day. Worker concurrency is a
# throughput setting; this is the admission control worker.py points at.
#
# Per account, not per book: an empresarial login managing fifty client books is
# one customer, and a per-book cap would sell fifty queues for the price of one.
# The tiers below the two named here never reach this route (needs_trader).
#
# ponytail: a concurrency cap, not a monthly allowance. It protects the box,
# which is the thing that actually breaks; a run/month meter is a billing
# question and wants a billing period, a counter and a reset — build it when
# runs cost money rather than latency.
BACKTEST_QUOTA = {"trader": 3, ENTERPRISE: 10}


async def inflight_backtests(session: AsyncSession, user) -> int:
    """Runs not yet finished across every book the account owns."""
    managed = select(Portfolio.id).where(Portfolio.owner_id == user.id)
    return await session.scalar(
        select(func.count()).select_from(BacktestRun).where(
            BacktestRun.status.in_(("pending", "running")),
            (BacktestRun.tenant_id == user.id) | BacktestRun.tenant_id.in_(managed),
        )
    )


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
    user, tid = tenant
    # Before the gallery: that call shells into the engine venv, and a queue
    # already full has no reason to spend an interpreter on name validation.
    limit = BACKTEST_QUOTA.get(effective_plan(user), 0)
    running = await inflight_backtests(session, user)
    if running >= limit:
        raise HTTPException(429, detail={
            "error": "backtest_quota", "limit": limit, "running": running,
            "message": (f"{running} backtests já na fila — aguarde uma terminar "
                        f"(limite do seu plano: {limit})"),
        })

    known = {s["name"] for s in await _gallery(session, tid)}
    if body.strategy not in known:
        raise HTTPException(404, f"unknown strategy: {body.strategy}")
    run = BacktestRun(
        tenant_id=tid, strategy=body.strategy, status="pending",
        # isoformat, not the date object: this column is JSONB, and the engine
        # reads start/end back as strings.
        params={"params": body.params or {},
                "start": body.start.isoformat() if body.start else None,
                "end": body.end.isoformat() if body.end else None,
                "freq": body.freq},
    )
    session.add(run)
    await session.commit()

    # ponytail: per-request procrastinate pool — mirrors the imports path.
    async with tasks.app.open_async():
        await tasks.run_backtest.defer_async(run_id=str(run.id))
    return _run_dict(run)


@backtests_router.get("")
async def list_backtests(
    # Was unbounded, and each row carries a metrics JSONB blob — the listing grew
    # with every run the tenant had ever submitted. Already newest-first, so a
    # capped page is the recent runs; served by ix_backtest_runs_tenant_created.
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (
        await session.scalars(
            select(BacktestRun).where(BacktestRun.tenant_id == tid)
            .order_by(BacktestRun.created_at.desc())
            .limit(limit)
            .offset(offset)
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
# count(*) + count(DISTINCT ticker) over `prices` is an aggregate across the whole
# table (89 ms at 257k rows, and it grows linearly with the plane). The dashboard
# polls this endpoint on a timer from every open tab, so the cost used to scale
# with the number of people looking at it. One cached answer per TTL decouples the
# two: the plane only changes when the ingestion pipeline runs, which is a manual,
# infrequent job.
_MARKET_STATUS_TTL = datetime.timedelta(seconds=60)
_market_status_cache: tuple[datetime.datetime, dict] | None = None


async def _last_ingest(session: AsyncSession) -> dict[str, dict]:
    """Latest ingestion attempt per kind. `error` is deliberately not exposed —
    it carries subprocess output with server paths (privacy._INTERNAL_COLUMNS
    strips the same field for the same reason); `status` is the part a caller
    needs. Never fails the status call: knowing the plane is stale matters more
    than knowing why."""
    out: dict[str, dict] = {}
    try:
        # DISTINCT ON, not "read everything and keep the first of each kind in
        # Python". ingest_runs grows by a row per ingestion attempt and never
        # shrinks, and this endpoint is polled on a timer from every open tab, so
        # the old form made the cost of answering "is the plane fresh?" scale with
        # how often the plane had been refreshed. ix_ingest_runs_kind_started
        # already indexes exactly this; only the query was wrong.
        #
        # The column list is explicit so `error` never leaves the database — it
        # carries subprocess output with server paths.
        rows = (await session.execute(
            select(IngestRun.kind, IngestRun.status,
                   IngestRun.started_at, IngestRun.finished_at)
            .distinct(IngestRun.kind)
            .order_by(IngestRun.kind, IngestRun.started_at.desc())
        )).all()
    except Exception:
        return out
    for kind, status, started_at, finished_at in rows:
        out[kind] = {
            "status": status,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
        }
    return out


# What an absent market plane looks like. A fresh deploy has no `prices` table at
# all until the first ingest runs, and aggregating a missing table raises — so the
# status endpoint used to 500 on exactly the environment that most needs to report
# "there is no data yet". Same posture as /positions and /price-history: the plane
# is optional, its absence is a fact to report, not an error.
_EMPTY_PLANE = {
    "prices": {"rows": 0, "tickers": 0, "min_date": None, "max_date": None},
    "tables": {},
}


async def _market_status_uncached() -> dict:
    pool = await get_engine_pool()
    prices = await pool.fetchrow(
        "SELECT count(*) AS rows, count(DISTINCT ticker) AS tickers, "
        "min(date) AS min_date, max(date) AS max_date FROM prices"
    )
    tables = {}
    for name, datecol in (("corporate_actions", "event_date"),
                          ("fundamentals_pit", "filing_date")):
        row = await pool.fetchrow(
            f"SELECT count(*) AS rows, max({datecol}) AS fresh FROM {name}"
        )
        tables[name] = {
            "rows": row["rows"],
            "fresh": row["fresh"].isoformat() if row["fresh"] else None,
        }
    return {
        "prices": {
            "rows": prices["rows"],
            "tickers": prices["tickers"],
            "min_date": prices["min_date"].isoformat() if prices["min_date"] else None,
            "max_date": prices["max_date"].isoformat() if prices["max_date"] else None,
        },
        "tables": tables,
    }


# Prices are expected every trading day. Four calendar days absorbs a long weekend
# plus a feriado without crying wolf; past that the plane is genuinely behind.
_PRICES_STALE_AFTER_DAYS = 4


@market_router.get("/status")
async def market_status(
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Health of the engine market plane (plain SQL over ENGINE_DATABASE_URL).
    0-row tables are reported honestly (fresh: null). Cached for _MARKET_STATUS_TTL.

    `stale` is the field worth acting on: this plane sat seven months behind while
    every position was marked from it, and nothing said so.
    """
    global _market_status_cache
    now = datetime.datetime.now(datetime.UTC)
    if _market_status_cache and now - _market_status_cache[0] < _MARKET_STATUS_TTL:
        payload = _market_status_cache[1]
    else:
        try:
            payload = await _market_status_uncached()
        except Exception:
            # Not cached: an unreachable plane should recover on the next call
            # rather than be pinned as empty for the whole TTL.
            return {**_EMPTY_PLANE, "prices_age_days": None, "stale": True,
                    "last_ingest": await _last_ingest(session)}
        _market_status_cache = (now, payload)

    max_date = payload["prices"]["max_date"]
    age_days = (
        (now.date() - date.fromisoformat(max_date)).days if max_date else None
    )
    # Not cached with the payload: ingest rows change on their own schedule, and
    # this is one indexed query against the platform DB.
    return {
        **payload,
        "prices_age_days": age_days,
        "stale": age_days is None or age_days > _PRICES_STALE_AFTER_DAYS,
        "last_ingest": await _last_ingest(session),
    }
