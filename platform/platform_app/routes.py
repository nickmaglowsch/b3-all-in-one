"""Tenant-scoped data-plane endpoints: imports, portfolio, IRPF.

Every read is scoped by tenant_id from get_current_tenant — never trust an id in the
request. Routers + task functions only (no service/repository layers).
"""
from __future__ import annotations

import asyncio
import datetime
import io
import json
import math
import shutil
import uuid
import zipfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import (
    b3run, darf, enginerun, market_planes, portfolio_sleeves, rebalance_plan, specstore,
    subprocs, tasks,
)
from platform_app.billing import (
    EMPRESARIAL, entitled, gate_detail, needs_bench, needs_mercado,
)
from platform_app.config import settings
from platform_app.db import get_async_session, get_engine_pool, xact_lock
from platform_app.jobstatus import BACKTEST_INFLIGHT, BacktestStatus, ImportStatus
from platform_app.models import (
    AssetClassOverride, BacktestRun, CashEvent, CostBasisOverride, DarfPayment,
    Import, IngestRun, Portfolio, PortfolioSleeve, Position, RebalanceSettings,
    TaxSummary, Trade, UserStrategy,
)
from platform_app.portfolio_sleeves import SleeveIn, sleeve_out
from platform_app.routes_portfolio_index import bounded, current_rf_marks
from platform_app.routes_portfolio_settings import _settings_dict
from platform_app.strategy_refs import _cascade_rename, _refuse_if_used
from platform_app.strategy_specs import (
    _REBALANCE, StrategyIn, _compile_portfolio, _compile_rank, check_live_refs,
    check_sleeve_refs, live_sleeves, off_b3_refs,
)
from platform_app.tenancy import get_current_tenant, get_optional_tenant, get_owned

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
            kind=_detect_kind(name), status=ImportStatus.PENDING,
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

    # One job for the whole batch, and per tenant (see defer_process_imports).
    # ponytail: per-request procrastinate pool — imports are rare human actions.
    async with tasks.app.open_async():
        await tasks.defer_process_imports(tid)

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
            "error": subprocs.redact(i.error), "parser_version": i.parser_version,
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
_TRADE_SORTS = {c: getattr(Trade, c) for c in (
    "date", "side", "market", "broker", "ticker", "qty", "price", "amount")}


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

@portfolio_router.get("/sleeves")
async def get_sleeves(
    tenant=Depends(needs_mercado),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (await session.scalars(
        select(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid)
        .order_by(PortfolioSleeve.strategy_ref, PortfolioSleeve.market, PortfolioSleeve.ticker)
    )).all()
    return [sleeve_out(s.strategy_ref, s.ticker, s.market, s.target_pct) for s in rows]


@portfolio_router.put("/sleeves")
async def put_sleeves(
    body: list[SleeveIn],
    tenant=Depends(needs_mercado),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Replace the whole sleeve set atomically. Validates percentages sum to 100
    (±0.01), every strategy_ref is 'CASH_CDI' or a real gallery spec and every
    ticker is priced on its market's plane (portfolio_sleeves). Saving a
    fundamentals-needing strategy is allowed even when none are ingested yet (data
    may arrive later) — the response just carries a warning."""
    _user, tid = tenant
    keys = [s.key for s in body]
    if dup := sorted({k for k in keys if keys.count(k) > 1}):
        raise HTTPException(422, f"sleeve repetido no conjunto: {dup[0]}")
    refs = [s.strategy_ref for s in body if s.strategy_ref]
    total = sum(s.target_pct for s in body)
    if abs(total - 100.0) > 0.01:
        raise HTTPException(422, f"sleeve percentages must sum to 100 (got {total:g})")
    # CASH_CDI is the risk-free sleeve; anything else must be a real gallery spec
    # (built-ins + this tenant's user strategies).
    gallery = {s["name"]: s for s in await _gallery(session, tid)}
    check_live_refs(refs, gallery)
    warnings = await portfolio_sleeves.check_tickers(body)  # 422 on an unpriced ticker

    await xact_lock(session, "sleeves", tid)
    await session.execute(delete(PortfolioSleeve).where(PortfolioSleeve.tenant_id == tid))
    for s in body:
        session.add(PortfolioSleeve(tenant_id=tid, strategy_ref=s.strategy_ref,
                                    ticker=s.ticker, market=s.market, target_pct=s.target_pct))
    await session.commit()

    # Soft warning (no hard-refuse): chosen strategy needs fundamentals and none
    # are ingested yet. Only touches the engine DB when it can actually matter.
    needs = [r for r in refs if gallery.get(r, {}).get("needs_fundamentals")]
    if needs and not await _fundamentals_available():
        warnings.append(_needs_funds_msg(needs))

    return {
        "sleeves": [sleeve_out(s.strategy_ref, s.ticker, s.market, s.target_pct) for s in body],
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


def _needs_funds_msg(strategies: list[str]) -> str:
    """pt-BR message naming the strategy/strategies that need fundamentals data."""
    uniq = list(dict.fromkeys(strategies))
    names = ", ".join(f"'{s}'" for s in uniq)
    who = (f"A estratégia {names} precisa" if len(uniq) == 1
           else f"As estratégias {names} precisam")
    return f"{who} de dados fundamentalistas, que ainda não foram ingeridos neste ambiente."


async def _fundamentals_available() -> bool:
    """True when fundamentals_pit has any rows in the engine market plane.
    Same DSN/pattern as market_status — the actual-availability check the guards
    key on, so a strategy 'just works' the moment fundamentals are ingested."""
    pool = await get_engine_pool()
    # EXISTS, not count(*): the question is "any rows?", and count scans them all.
    return bool(await pool.fetchval("SELECT EXISTS (SELECT 1 FROM fundamentals_pit)"))


async def _advise(**kwargs) -> dict:
    try:
        return await subprocs.offload(enginerun.advise_weights, **kwargs)
    except enginerun.EngineNeedsFundamentals:
        raise  # callers turn this into a clean 422 (never a 500)
    except RuntimeError as e:
        raise HTTPException(500, subprocs.report("advise_weights", str(e)))


async def _advise_strategy(strategy: str, freq: str, extra_env: dict | None = None) -> dict:
    """Advise a strategy's target weights at the given rebalance cadence (freq).
    freq='ME' is the default path and passes no params; a non-default cadence is
    threaded as ``--params rebalance_freq`` through the engine script. extra_env
    points the registry at the tenant's user specs when the strategy is a user ref."""
    params = None if freq == "ME" else {"rebalance_freq": freq}
    return await _advise(strategy=strategy, params=params, extra_env=extra_env)


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


async def _combined_target(
    session: AsyncSession, tid: uuid.UUID, freq: str = "ME"
) -> tuple[dict[str, float], float, dict[str, str], float]:
    """Blend the tenant's sleeves into (combined_weights, cash_weight_target,
    off_b3, b3_assets): combined[t] = Σ sleeve_pct/100 × sleeve_weights[t], where a ticker
    sleeve's weights are just {ticker: 1}; CASH_CDI pct becomes the cash weight;
    off_b3 is {ticker: market} for the US/crypto tickers advise_weights prices off
    their own plane; b3_assets is the weight of the sleeves holding B3 assets
    (portfolio_sleeves.SleeveSplit). 409 when no sleeves are configured. `freq`
    is the rebalance cadence passed to each strategy sleeve (default 'ME')."""
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
    extra_env = specstore.specs_env(user_rows, tid)
    user_upd = {r.name: r.updated_at.isoformat() if r.updated_at else "" for r in user_rows}

    today = date.today().isoformat()

    def _key(ref: str) -> tuple:
        if ref in user_upd:
            return (ref, today, freq, str(tid), user_upd[ref])
        return (ref, today, freq)

    # Split cash and tickers out first: CASH_CDI is the risk-free sleeve and a
    # ticker is its own target weight, so neither has anything to advise and
    # neither may reach the engine as a strategy.
    cash_weight, by_ref, combined, off_b3, b3_assets = portfolio_sleeves.split_sleeves(sleeves)
    equity = [(ref, pct, _key(ref)) for ref, pct in by_ref]  # (sleeve_ref, pct, cache_key)

    # Each sleeve is a separate engine subprocess that builds its own market
    # plane, and they are independent — one sleeve's weights depend only on its
    # own strategy. Awaiting them in a loop made a cold three-sleeve portfolio
    # pay three full plane builds end to end. Gathered, the wall clock is the
    # slowest sleeve. Cache hits are resolved before dispatch so a warm sleeve
    # costs nothing — and copied out, not re-read after the await: any other
    # request's bounded() can empty the shared dict meanwhile, and a hit gone
    # missing read as "needs fundamentals" (a 422 for a strategy that needs none).
    advised = {key: hit for _ref, _pct, key in equity
               if (hit := _SLEEVE_WEIGHTS_CACHE.get(key)) is not None}
    todo = [(ref, key) for ref, _pct, key in equity if key not in advised]
    if todo:
        await session.commit()  # no connection held across the subprocesses
        results = await asyncio.gather(
            *(_advise_strategy(ref, freq, extra_env or None) for ref, _key_ in todo),
            return_exceptions=True,
        )
        for (ref, key), res in zip(todo, results):
            if isinstance(res, BaseException):
                continue          # classified below, so one failure is not fatal here
            advised[key] = bounded(_SLEEVE_WEIGHTS_CACHE, _CACHE_MAX)[key] = res
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
        adv = advised.get(key)
        if adv is None:
            exc = failures.get(ref)
            missing.append(getattr(exc, "strategy", None) or ref)
            continue
        for t, w in adv["weights"].items():
            combined[t] = combined.get(t, 0.0) + pct * float(w)
    if missing:
        raise HTTPException(422, _needs_funds_msg(missing))
    return combined, cash_weight, off_b3, b3_assets


async def _positions_json(session: AsyncSession, tid: uuid.UUID) -> dict[str, dict]:
    rows = (await session.scalars(
        select(Position).where(Position.tenant_id == tid).order_by(Position.ticker)
    )).all()
    return {p.ticker: {"qty": float(p.qty or 0.0), "avg_cost": float(p.avg_cost or 0.0)}
            for p in rows if p.ticker and (p.qty or 0.0) != 0}


@portfolio_router.get("/target")
async def get_target(
    tenant=Depends(needs_mercado),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Combined target weights, current weights, and per-ticker drift. Prices the
    book via one advise_weights --weights valuation pass (same basis as the
    rebalance trades). The drift is the B3 book against its own share of the
    target; off-B3 sleeves are listed with no current position (rebalance_plan)."""
    _user, tid = tenant
    combined, cash_weight, off_b3, b3_assets = await _combined_target(session, tid)
    split = rebalance_plan.split_target(combined, cash_weight, off_b3, b3_assets)
    positions = await _positions_json(session, tid)
    cash_cdi = await _cash_cdi_tickers(session, tid)

    priced = await _advise(weights=split.b3, positions=positions, nav_cash=0.0)
    trades = priced.get("trades") or {}
    nav = float(trades.get("nav", 0.0))
    valued = trades.get("positions_valued") or {}
    # Feature 3: a cash/CDI-marked holding counts as cash — drop it from the equity
    # current weights/drift so it isn't shown as a big "sell everything" row; its
    # value stays in NAV, so it surfaces in the residual cash weight below.
    current = {t: v / nav for t, v in valued.items()
               if t.upper() not in cash_cdi} if nav > 0 else {}

    drift = rebalance_plan.drift_rows(split, current, nav, off_b3)

    # Residual (non-equity) weight = actual uninvested cash + any cash/CDI holdings.
    cash_weight_current = max(0.0, 1.0 - sum(current.values())) if nav > 0 else 0.0

    return {
        "as_of": priced["as_of"],
        "combined_weights": combined,
        "current_weights": current,
        # Of the B3 book, as current_weights and the drift are; a book held as
        # it is (no B3 asset sleeve) targets the cash it has.
        "cash_weight_target": cash_weight_current if split.hold_b3 else split.cash,
        "cash_weight_current": cash_weight_current,
        "drift": drift,
        "nav": nav,
        "markets": off_b3,
        "warnings": rebalance_plan.target_warnings(split, off_b3, trades),
    }


class RebalanceRequest(BaseModel):
    # An aporte, never a withdrawal: a negative one would have the off-B3 share
    # "sell" assets the plan cannot see.
    new_cash: float = Field(0.0, ge=0, allow_inf_nan=False)
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
    tenant=Depends(needs_mercado),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Advisor v0 (single-shot, this month): combined target → integer trade list →
    tax overlay via the TLH machinery. Advisory only; nothing executes.

    Per-tenant settings (spec/05 Features 3 & 4; defaults = today's behavior):
      · cash/CDI holdings are pinned as cash (not sold, not an equity trim);
      · buy_only_laggards → no trim sells, deploy cash only into underweight names;
      · sell_only_for_harvest → keep only sells that realize a loss;
      · cadence → the rebalance freq passed to the combined target.
    US / crypto sleeves are bought only with their share of new_cash and never
    cause a B3 trade (rebalance_plan.split_target)."""
    _user, tid = tenant
    settings_ = _settings_dict(await session.get(RebalanceSettings, tid))
    cadence = settings_["cadence"]
    buy_only = settings_["buy_only_laggards"]
    harvest_only = settings_["sell_only_for_harvest"]

    combined, cash_weight, off_b3, b3_assets = await _combined_target(
        session, tid, freq=cadence)
    # v0: pins and excludes collapse to one "freeze" set — both mean "don't trade
    # this ticker, keep its current position" (per-ticker keep-in-place).
    frozen = {t.upper() for t in (body.exclude + body.pins)}
    # From here the B3 book's own target, and its own share of the aporte; an
    # excluded/pinned off-B3 sleeve's share (off_kept) stays as cash.
    split = rebalance_plan.split_target(combined, cash_weight, off_b3, b3_assets)
    combined, cash_weight = split.b3, split.cash
    new_cash, off_cash, off_kept = split.aporte(body.new_cash, frozen)
    positions = await _positions_json(session, tid)
    held = set(positions)
    cash_cdi = await _cash_cdi_tickers(session, tid)
    cash_cdi_held = {t for t in held if t.upper() in cash_cdi}
    # With no sleeve holding a B3 asset, the whole B3 book is held as it is.
    if split.hold_b3:
        frozen |= {t.upper() for t in held}

    target = combined
    if frozen or cash_cdi_held:
        # One valuation pass (no new_cash) to learn current values, so frozen and
        # cash/CDI holdings can be pinned at their current weight.
        val = await _advise(weights=combined, positions=positions, nav_cash=0.0)
        vtrades = val.get("trades") or {}
        valued = {k.upper(): v for k, v in (vtrades.get("positions_valued") or {}).items()}
        final_nav = float(vtrades.get("nav", 0.0)) + new_cash
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

    priced = await _advise(weights=target, positions=positions, nav_cash=new_cash)
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
    unfunded: list[str] = []  # buys the budget ran out before
    if buy_only:
        budget = new_cash + sum(float(s.get("est_brl", 0.0)) for s in sells)
        buys, unfunded = rebalance_plan.cap_buys(buys, budget)
    off_trades = await _off_b3_buys(split, off_b3, off_cash, frozen)
    buys += off_trades.get("buys", [])

    # Tax overlay: run the plan's sells (only B3 tickers actually held) through
    # tlh_plan; an off-B3 sale is not apurated by B3 rules (plan_notes says so).
    # A book with no B3 history (sleeves of US / crypto tickers only, nothing
    # imported) has no apuração to overlay, and tlh_plan cannot build an empty one.
    has_book = await session.scalar(select(exists().where(Trade.tenant_id == tid)))
    tax = await _run_tlh_plan(session, tid, [(s["ticker"], s["qty"]) for s in sells if
                                             s["ticker"] in held and s["ticker"] not in off_b3]
                              ) if has_book else {}

    notes = rebalance_plan.plan_notes(split, off_b3, body.new_cash, {
        "buys": buys,
        **{k: sorted({*trades.get(k, []), *off_trades.get(k, [])})
           for k in ("unpriced_skipped", "below_one_lot")}}, unfunded, frozen)
    acao = (tax.get("buckets") or {}).get("AÇÃO") or {}
    limit = float(tax.get("isencao_limit", 20_000.0))
    if float(acao.get("gross_after", 0.0)) > limit:
        notes.append(
            f"ações gross sales this month would exceed R$20k isenção "
            f"(gross pós-vendas R$ {acao['gross_after']:,.2f} > R$ {limit:,.0f})")

    return {
        "as_of": priced["as_of"],
        # The B3 book plus the whole aporte: the share headed abroad, and the
        # excluded/pinned off-B3 share kept as cash.
        "nav": nav + off_cash + off_kept,
        "new_cash": body.new_cash,
        "cadence": cadence,
        "settings": settings_,
        "trades": {"sells": sells, "buys": buys},
        "tax": tax,
        "notes": notes,
    }


async def _off_b3_buys(split, off_b3: dict[str, str], cash: float, frozen: set[str]) -> dict:
    """The off-B3 sleeves' buys, sized from their share of the aporte alone, in
    reais off each one's own plane; an excluded/pinned one keeps its share as
    cash. {} with no aporte: nothing is ever sold to buy them."""
    # Of what buys them, each in proportion to its weight in the target.
    buying = {t: w for t, w in split.off.items() if t not in frozen}
    if cash <= 0 or not buying:
        return {}
    weights = {t: w / sum(buying.values()) for t, w in buying.items()}
    out = await _advise(weights=weights, markets={t: off_b3[t] for t in weights},
                        positions={}, nav_cash=cash)
    return out.get("trades") or {}


class IrpfRequest(BaseModel):
    year: int = 2025


# IRPF export headers must mirror the canonical CSVs byte-for-byte so irpf_2026 reads
# them exactly as it reads taxcore/data/*.csv (source_file -> __source_file column).
_TRADE_HEADER = ["date", "side", "market", "maturity", "broker", "ticker",
                 "qty", "price", "amount", "__source_file", "signed_qty"]
_EVENT_HEADER = ["date", "ticker", "event_type", "event_category", "is_amortization",
                 "qty", "price", "amount", "broker", "__source_file"]
_POSITION_HEADER = ["ticker", "qty", "avg_cost", "total_cost", "market"]


async def _book_csv_rows(
    session: AsyncSession, tid: uuid.UUID
) -> tuple[list[list], list[list]]:
    """``(trade_rows, event_rows)`` for a taxcore workspace, materialised here.

    Plain attribute reads off already-loaded ORM instances (microseconds), done
    on the loop so the worker thread that writes the CSVs never touches a
    Session. Shared by the IRPF report, the DARF apuração and the TLH plan so
    they can never write differently-shaped books for taxcore to read.
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


def _write_book(ws: Path, trade_rows: list[list], event_rows: list[list]) -> None:
    """The two CSVs every irpf_2026 / tlh_plan run reads (build_timeline)."""
    b3run.write_csv(ws / "data" / "trades.csv", _TRADE_HEADER, trade_rows)
    b3run.write_csv(ws / "data" / "cash_events.csv", _EVENT_HEADER, event_rows)


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

    override_rows = tasks._override_rows(overrides)
    extracts = [(imp.stored_path, imp.filename) for imp in movs]
    await session.commit()  # everything is read; no connection held across the run

    def _work():
        """Workspace build + subprocess + teardown, off the event loop.

        Serialising a full trade history and copying every uploaded extract used
        to run inline. The API is one uvicorn process with no --workers, so that
        stalled every concurrent request for the duration.
        """
        with b3run.workspace(tid, "b3irpf-") as ws:
            _write_book(ws, trade_rows, event_rows)
            b3run.write_csv(ws / "data" / "cost_basis_overrides.csv",
                            tasks._OVERRIDE_HEADER, override_rows)
            for stored_path, filename in extracts:
                if Path(stored_path).exists():
                    shutil.copy(stored_path, ws / "b3_imports" / filename)
            return b3run.run_module(ws, ["scripts.irpf_2026", "--year", str(body.year)])

    proc = await subprocs.offload(_work)
    if proc.returncode != 0:
        raise HTTPException(500, subprocs.report("irpf_2026", proc))
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

# The arithmetic — one DARF 6015 a month, its due date — lives in darf.py.
DARF_CLASSES, DARF_CODIGO, _darf_vencimento = darf.DARF_CLASSES, darf.DARF_CODIGO, darf.vencimento


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
    """The year's per-class apuração rows (see _run_irpf_json)."""
    return (await _run_irpf_json(session, tid, year)).get("apuracao") or []


async def _run_irpf_json(session: AsyncSession, tid: uuid.UUID, year: int) -> dict:
    """taxcore's Renda Variável apuração and monthly DARFs for the year, as data.

    Far cheaper than POST /irpf/reports even though it is the same script:
    ``--json`` returns before Bens e Direitos and the renda fixa sections, which
    are what parse every uploaded Excel file. This needs only trades and
    cash_events, so the workspace copies no extracts.
    """
    trade_rows, event_rows = await _book_csv_rows(session, tid)
    if not trade_rows:
        # build_timeline over an empty book has no columns to sort. An empty
        # ledger is the honest answer anyway — nothing was sold, nothing is due.
        return {}

    await session.commit()  # no connection held across the run

    def _work():
        with b3run.workspace(tid, "b3darf-") as ws:
            _write_book(ws, trade_rows, event_rows)
            return b3run.run_module(
                ws, ["scripts.irpf_2026", "--year", str(year), "--json"])

    proc = await subprocs.offload(_work)
    if proc.returncode != 0:
        raise HTTPException(500, subprocs.report("irpf_2026 --json", proc))
    return subprocs.last_json(proc.stdout)


@irpf_router.get("/darf")
async def darf_ledger(
    year: int | None = Query(None, ge=2000, le=2100),
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """The year's DARFs: what the engine says is owed, what the tenant recorded.

    One row per month — every class goes on the one DARF 6015 — listed when the
    month owes something, carries an amount below R$ 10 forward, or already has
    a recorded payment. The full twelve-month grid, including the quiet months, is the
    IRPF report's job; here a zero row would be one nobody can act on and one
    that reads like an unpaid one.
    """
    _user, tid = tenant
    year = year or date.today().year
    pagamentos: dict[datetime.date, list[dict]] = {}
    for p in await _darf_payments(session, tid, year):
        pagamentos.setdefault(p.apuracao, []).append(_payment_dict(p))
    # Brasília's day, not the container's UTC one: from 21h on the due date UTC
    # is already tomorrow, and a DARF still payable read "atrasado". Fixed -03:00
    # is exact — Brazil has had no daylight saving since 2019.
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3))).date()
    return darf.livro(year, await _run_irpf_json(session, tid, year), pagamentos, today)


class DarfPaymentIn(BaseModel):
    apuracao: str  # "AAAA-MM"
    classe: str = "TOTAL"  # one DARF a month; a per-class name is legacy
    valor_principal: float = Field(allow_inf_nan=False)
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

    await xact_lock(session, "darf", tid)
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
    ticker: str = Field(max_length=32)
    qty: float = Field(allow_inf_nan=False)


class TlhRequest(BaseModel):
    # Both reach tlh_plan as single argv elements; bounded far inside 128 KB.
    sells: list[TlhSell] = Field([], max_length=500)
    exclude: list[str] | None = Field(None, max_length=500)


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
    trade_rows, event_rows = await _book_csv_rows(session, tid)
    positions = (await session.scalars(
        select(Position).where(Position.tenant_id == tid).order_by(Position.ticker)
    )).all()
    # Materialised on the loop for the same reason as _book_csv_rows: the worker
    # thread below never touches a Session.
    position_rows = [
        [p.ticker, p.qty, p.avg_cost, p.total_cost, p.market] for p in positions]
    # asyncpg, so it has to run on the loop rather than inside the thread.
    price_rows = await _latest_engine_prices([p.ticker for p in positions])
    await session.commit()  # no connection held across the run

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
        with b3run.workspace(tid, "b3tlh-") as ws:
            # tlh_plan reads: data/trades.csv + data/cash_events.csv (build_timeline),
            # data/positions.csv (canonical holdings), and the latest price snapshot in
            # each of data/{snapshots,fii_snapshots,external_prices} (via _common.price_lookup).
            # It does NOT read cost_basis_overrides. cash_events header must mirror the
            # canonical CSV (source_file -> __source_file) like the IRPF route.
            _write_book(ws, trade_rows, event_rows)
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

    proc = await subprocs.offload(_work)
    if proc.returncode == 2:  # validation error (bad/oversized sell)
        raise HTTPException(422, (proc.stderr or proc.stdout).strip()[-2000:])
    if proc.returncode != 0:
        raise HTTPException(500, subprocs.report("tlh_plan", proc))
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
    tenant=Depends(needs_mercado),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    return await _tax_summary_payload(session, tid)


async def _book_version(session: AsyncSession, tid: uuid.UUID) -> int | None:
    """max(Trade.id): a full rebuild is delete+insert and ids come from a
    sequence, so every rebuild moves it (to None for a book emptied). None =
    no trades."""
    return await session.scalar(select(func.max(Trade.id)).where(Trade.tenant_id == tid))


# One apuração per tenant at a time. Each is a multi-second subprocess that
# dumps the whole book to CSV while holding a pooled connection, and the button
# that fires it sits on a page-load surface — two tabs, or a held-down click,
# is enough to stack them. In-process only: this is a single-API-container
# deployment, and the upsert below is what keeps correctness when it is not.
_tax_refresh_locks: dict[uuid.UUID, asyncio.Lock] = {}


@portfolio_router.post("/tax-summary/refresh")
async def refresh_tax_summary(
    tenant=Depends(needs_mercado),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    book = await _book_version(session, tid)
    if book is None:
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

        # process_imports replaces the book and drops this row under the same
        # lock, so a rebuild either landed before the check below or waits for
        # this commit and then drops what it stored. Unlocked, one landing while
        # tlh_plan ran was overwritten by a summary of the book it replaced.
        await xact_lock(session, "imports", tid)
        if await _book_version(session, tid) != book:
            await session.rollback()
            return {"payload": payload, "computed_at": started.isoformat(),
                    "stale": True, "stale_reason": "import"}
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
_SPEC_CACHE: dict[str, dict | None] = {}                 # built-in name -> spec (None: none)


async def _list_json(script: str, extra_env: dict | None = None) -> list:
    """Shell an engine list_*.py script and parse the JSON array it prints."""
    proc = await subprocs.offload(enginerun.run_script, f"{script}.py", [], extra_env)
    if proc.returncode != 0:
        raise HTTPException(500, subprocs.report(script, proc))
    return json.loads(proc.stdout)


async def _builtins() -> list[dict]:
    """The built-in gallery (no ENGINE_USER_SPECS_DIR), tagged source=builtin."""
    global _STRATEGIES_CACHE
    if _STRATEGIES_CACHE is None:
        _STRATEGIES_CACHE = [
            {**s, "source": "builtin"} for s in await _list_json("list_strategies")]
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
    listed = await _list_json("list_strategies", specstore.specs_env(rows, tid))
    user_names = {r.name for r in rows}
    merged = [{**s, "source": "user" if s["name"] in user_names else "builtin"}
              for s in listed]
    bounded(_TENANT_GALLERY, _CACHE_MAX)[tid] = (fingerprint, merged)
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
        _SIGNALS_CACHE = await _list_json("list_signals")
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
    an authed tenant's own spec-kind strategies resolve too (env-var, like _gallery).

    Public, and every miss is a whole engine interpreter. A built-in's spec only
    changes with the image, so it is answered once per process; a name that is
    no built-in can only be the caller's own strategy, so anonymous callers get
    their 404 without a subprocess. User names never shadow a built-in
    (_compile_and_validate refuses the collision), so the cache is global."""
    builtin = name in {s["name"] for s in await _builtins()}
    if builtin and name in _SPEC_CACHE:
        spec = _SPEC_CACHE[name]
    elif builtin or tenant:
        extra_env = {} if builtin else await specstore.user_specs_env(session, tenant[1])
        proc = await subprocs.offload(
            enginerun.run_script, "get_strategy_spec.py", ["--name", name], extra_env)
        spec = json.loads(proc.stdout) if proc.returncode == 0 else None
        if builtin and proc.returncode in (0, 1):  # 1 is "no data spec"; not a timeout
            _SPEC_CACHE[name] = spec
    else:
        spec = None
    if spec is None:
        raise HTTPException(404, f"strategy {name!r} has no cloneable spec")
    return spec


# ── User-created strategies (spec/06) ─────────────────────────────────────────
# Pydantic validates the statically-checkable rules at the boundary (name, numeric
# ranges, rebalance enum, portfolio weights). The engine's validate_spec.py does the
# deep signal/factor validation and checks ticker sleeves against the price plane;
# the gallery resolves sleeve refs + the cycle guard.

async def _validate_spec_engine(spec: dict) -> dict:
    """Shell validate_spec.py; raise 422 on structured errors, return the report:
    needs_fundamentals (inferred from f_* refs) and warnings (a ticker's late first
    price)."""
    proc = await subprocs.offload(  # stdin: one argv element over 128 KB is E2BIG
        enginerun.run_script, "validate_spec.py", ["--spec-json", "-"], stdin=json.dumps(spec))
    try:
        report = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise HTTPException(500, subprocs.report("validate_spec", proc))
    if not report.get("ok"):
        raise HTTPException(422, "; ".join(report.get("errors") or ["invalid spec"]))
    return report


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
        user_rank = {r.name for r in rows if r.kind == "rank_and_hold"}
        check_sleeve_refs(body, resolvable=builtin_names | user_rank | {"CASH_CDI"},
                          user_portfolios={r.name for r in rows if r.kind == "fixed_weight"
                                           and r.id != exclude_id},
                          off_b3=off_b3_refs(await _builtins(), rows))
        spec = _compile_portfolio(body)

    report = await _validate_spec_engine(spec)
    needs_funds = bool(report.get("needs_fundamentals"))
    if needs_funds:
        spec["needs_fundamentals"] = True  # persist inferred flag for gallery/engine
    warnings: list[str] = list(report.get("warnings") or [])
    if needs_funds and not await _fundamentals_available():
        warnings.append(_needs_funds_msg([name]))
    return spec, warnings


def _user_strategy_dict(r: UserStrategy) -> dict:
    return {
        "id": str(r.id), "name": r.name, "kind": r.kind, "spec": r.spec,
        "source": "user", "origin": r.origin,  # "mcp": saved by an AI (spec/09)
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _get_user_strategy(sid: str, tid: uuid.UUID, session: AsyncSession) -> UserStrategy:
    return await get_owned(session, UserStrategy, sid, tid, "strategy not found")


@strategies_router.get("/user")
async def list_user_strategies(
    tenant=Depends(needs_bench),
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
    tenant=Depends(needs_bench),
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


@strategies_router.put("/user/{sid}")
async def update_user_strategy(
    sid: str,
    body: StrategyIn = Body(...),
    tenant=Depends(needs_bench),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    row = await _get_user_strategy(sid, tid, session)
    if body.kind != row.kind:
        raise HTTPException(422, "cannot change a strategy's kind — delete and recreate")
    # Portfolios and the live target run on B3: a sleeve may not move off it.
    if body.kind == "rank_and_hold" and (body.market or "B3") != "B3":
        await _refuse_if_used(session, tid, row, f" — it cannot run on {body.market}")
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
    tenant=Depends(needs_bench),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    row = await _get_user_strategy(sid, tid, session)
    await _refuse_if_used(session, tid, row)
    await session.delete(row)
    await session.commit()
    _TENANT_GALLERY.pop(tid, None)
    return {"deleted": sid}


@strategies_router.post("/user/{sid}/promote")
async def promote_user_strategy(
    sid: str,
    tenant=Depends(needs_bench),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Compile a portfolio strategy's sleeves into the live target (reuses the
    existing PUT /portfolio/sleeves logic — same validation + atomic replace).
    That route is gated on `mercado`, so this one is too: bench alone composes
    portfolios, it does not get to set the live target."""
    user, tid = tenant
    if "mercado" not in (have := entitled(user)):
        raise HTTPException(402, detail=gate_detail("mercado", have))
    row = await _get_user_strategy(sid, tid, session)
    if row.kind != "fixed_weight":
        raise HTTPException(422, "only portfolio (fixed_weight) strategies can be promoted")
    sleeves = [SleeveIn(**sl) for sl in live_sleeves(row.spec)]
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

    @field_validator("params")
    @classmethod
    def _bounded_params(cls, v: dict | None) -> dict | None:
        """`freq` omitted, the engine falls back to params["rebalance_freq"] —
        the same .resample() the enum above guards, reached around it. And the
        dict is stored as JSONB, which refuses NaN, then sent to the engine."""
        if v and v.get("rebalance_freq", "ME") not in _REBALANCE.__args__:
            raise ValueError(f"rebalance_freq must be one of {_REBALANCE.__args__}")
        if v and len(json.dumps(v, allow_nan=False)) > 64 * 1024:  # ValueError on NaN
            raise ValueError("params too large")
        return v


# Backtests one ACCOUNT may have queued or running at once. The worker runs two
# jobs at a time on a shared VPS (spec/01) and `queueing_lock` only stops one
# *kind* of job overlapping — so without this a single paying customer submitting
# a hundred runs owns both slots for the rest of the day. Worker concurrency is a
# throughput setting; this is the admission control worker.py points at.
#
# Per account, not per book: an empresarial login managing fifty client books is
# one customer, and a per-book cap would sell fifty queues for the price of one.
# Nobody without `bench` reaches this route (needs_bench), so 0 is unreachable.
#
# ponytail: a concurrency cap, not a monthly allowance. It protects the box,
# which is the thing that actually breaks; a run/month meter is a billing
# question and wants a billing period, a counter and a reset — build it when
# runs cost money rather than latency.
BACKTEST_QUOTA = {EMPRESARIAL: 10, "bench": 3}


async def inflight_backtests(session: AsyncSession, user) -> int:
    """Runs not yet finished across every book the account owns.

    Bounded to the last day: a run times out after subprocess_timeout_s, so one
    still "running" a day later is a row whose worker died with it, and letting
    it count would lock the account out of the bench for good."""
    managed = select(Portfolio.id).where(Portfolio.owner_id == user.id)
    return await session.scalar(
        select(func.count()).select_from(BacktestRun).where(
            BacktestRun.status.in_(BACKTEST_INFLIGHT),
            BacktestRun.created_at > func.now() - datetime.timedelta(days=1),
            (BacktestRun.tenant_id == user.id) | BacktestRun.tenant_id.in_(managed),
        )
    )


def _run_dict(r: BacktestRun) -> dict:
    return {
        "id": str(r.id), "strategy": r.strategy, "status": r.status,
        "error": subprocs.redact(r.error), "params": r.params, "metrics": r.metrics,
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
    limit = max((n for m, n in BACKTEST_QUOTA.items() if m in entitled(user)), default=0)
    # Held to the commit below, so N parallel POSTs cannot all count `limit - 1`.
    await xact_lock(session, "backtests", user.id)
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
        tenant_id=tid, strategy=body.strategy, status=BacktestStatus.PENDING,
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
    return await get_owned(session, BacktestRun, rid, tid, "backtest not found")


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
    rows = await asyncio.to_thread(b3run.read_csv_rows, path)
    # Column-oriented series: {"date": [...], "aftertax": [...], ...}. dates stay
    # strings; the rest parse to float, or null where pandas wrote NaN as "" (a
    # drawdown over a zero start is 0/0) or the value is not JSON (inf).
    cols = rows[0].keys() if rows else []
    series: dict[str, list] = {c: [] for c in cols}
    for r in rows:
        for c in cols:
            series[c].append(r[c] if c == "date" else _finite_or_none(r[c]))
    return {"id": run_id, "series": series}


def _finite_or_none(cell: str | None) -> float | None:
    try:
        v = float(cell)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


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
_EMPTY_PLANE = market_planes._EMPTY_PLANE


async def _market_status_uncached() -> dict:
    return await market_planes.status(await get_engine_pool())


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
