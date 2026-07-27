"""Historical portfolio index: the tenant's book as a daily time-weighted
return series, charted against IBOV and CDI (spec/03 "Benchmarking").

The other half of benchmarking — "same money, different strategy" — ships in
routes_whatif.py as a point comparison. This is the complementary view: what the
book the tenant *actually* holds did, day by day, with deposits and withdrawals
neutralised so the line is comparable to an index.

Holdings and flows come from the tenant's own tables; daily closes come from the
shared market plane over asyncpg (same source and failure posture as
/portfolio/price-history); IBOV and CDI come from the engine via a subprocess,
because the engine owns those fetchers and their cache. The chaining math lives
in portfolio_index.py, which is pure and unit-tested without a database.

Owned by the portfolio agent. Registered in main.py as `portfolio_index_router`.
"""
from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import enginerun, portfolio_index as pidx
from platform_app.db import get_async_session, get_engine_pool
from platform_app.models import CashEvent, Trade
from platform_app.tenancy import get_current_tenant

portfolio_index_router = APIRouter(prefix="/portfolio/index", tags=["portfolio-index"])


async def _book(session: AsyncSession, tid) -> tuple[list, list, list[str]]:
    """(qty_deltas, flows, excluded_markets) from the tenant's trades + cash_events.

    Trades carry `signed_qty` (buys +, sells −) and qty_events carry an already
    signed `qty` — both set by taxcore's load_b3, so no side parsing here.

    Flows are 4-tuples here, (date, kind, amount, ticker): the ticker is what
    `pidx.select_tickers` needs to keep an excluded asset's cash out of a
    filtered book. It strips the field back off before `build_index` sees it.

    Columns, not entities: this reads six fields off trades and five off
    cash_events, and loading whole ORM instances for a multi-year book built an
    object per row to throw all but those away.
    """
    trades = (await session.execute(
        select(Trade.date, Trade.ticker, Trade.market,
               Trade.signed_qty, Trade.amount, Trade.side)
        .where(Trade.tenant_id == tid)
    )).all()
    events = (await session.execute(
        select(CashEvent.date, CashEvent.event_category, CashEvent.ticker,
               CashEvent.qty, CashEvent.amount)
        .where(CashEvent.tenant_id == tid)
    )).all()

    qty_deltas: list[tuple[datetime.date, str, float]] = []
    flows: list[tuple[datetime.date, str, float, str | None]] = []
    excluded_markets: set[str] = set()

    for t_date, ticker, market, signed_qty, amount, side in trades:
        if not t_date or not ticker:
            continue
        if pidx.is_excluded(ticker, market):
            excluded_markets.add(market)
            continue
        canon = pidx.canonical(ticker, market)
        if signed_qty:
            qty_deltas.append((t_date, canon, float(signed_qty)))
        if amount is not None and side in ("Compra", "Venda"):
            kind = "buy" if side == "Compra" else "sell"
            flows.append((t_date, kind, abs(float(amount)), canon))

    for e_date, category, ticker, qty, amount in events:
        if not e_date:
            continue
        if category == "qty_event" and ticker and qty:
            qty_deltas.append((e_date, pidx.canonical(ticker), float(qty)))
        elif category in pidx.INCOME_CATEGORIES and amount is not None:
            flows.append((e_date, "income", abs(float(amount)),
                          pidx.canonical(ticker) if ticker else None))

    return qty_deltas, flows, sorted(excluded_markets)


# The trading calendar and the benchmark series are facts about the market, not
# about a tenant: every book asking about the same window gets the same answer,
# and the plane only moves when the ingestion pipeline runs. Both are keyed by
# the calendar day so an entry is never reused past the day it was written.
# ponytail: process-lifetime dicts, same lifecycle as routes._STRATEGIES_CACHE.
_GRID_CACHE: dict[tuple, list[datetime.date]] = {}
_BENCH_CACHE: dict[tuple, dict[str, list[float]]] = {}
_CACHE_MAX = 256


def _bounded(cache: dict) -> dict:
    """Every key carries a date, so entries are never reused past their day —
    without a bound the dict just grows by one entry per distinct window per day.
    Dropping the lot on overflow is fine; the next call refills what it needs."""
    if len(cache) > _CACHE_MAX:
        cache.clear()
    return cache


async def _fetch_grid(pool, start: datetime.date) -> list[datetime.date]:
    """Every date the plane has any price for, from `start`."""
    rows = await pool.fetch(
        "SELECT DISTINCT date FROM prices WHERE date >= $1 ORDER BY date", start)
    return [r["date"] for r in rows]


async def _trading_grid(start: datetime.date) -> list[datetime.date]:
    """The exchange calendar from `start`, cached for the day.

    `SELECT DISTINCT date FROM prices` is an index-only scan over most of the
    table (Postgres has no loose index scan before 18), and it was run on every
    page load to rediscover a calendar that changes once a day at most.
    """
    key = (start, datetime.date.today())
    grid = _GRID_CACHE.get(key)
    if grid is None:
        grid = await _fetch_grid(await get_engine_pool(), start)
        _bounded(_GRID_CACHE)[key] = grid
    return grid


async def _plane(tickers: list[str], start: datetime.date) -> tuple[list, dict]:
    """(grid, closes) from the market plane: its trading calendar from `start`,
    and raw closes per requested ticker.

    The grid is every date the plane has any price for — the exchange calendar
    as the plane knows it, so the index has a point on exactly the days a mark
    exists. Raises so the caller can turn an unreachable plane into a 503.
    """
    pool = await get_engine_pool()
    grid = await _trading_grid(start)
    # external_prices carries the instruments COTAHIST does not (listed
    # fixed-income ETFs — see engine/b3_pipeline/external_prices.py). Unioned into
    # the closes only, never into the grid above: the grid must stay the exchange
    # calendar as COTAHIST knows it, and B3's daily file shares that calendar, so
    # a union there could only add noise. UNION ALL is safe because the two tables
    # are disjoint by construction — a ticker is in COTAHIST or it is not.
    #
    # The table belongs to the engine, which creates it on its next pipeline run,
    # and the two deploy independently. Selecting it unconditionally would make
    # every index request 503 in the window between this code shipping and that
    # run — so the union is added only once the table is actually there.
    plane = ["SELECT ticker, date, close FROM prices "
             "WHERE ticker = ANY($1::text[]) AND date >= $2 AND close IS NOT NULL"]
    if await pool.fetchval("SELECT to_regclass('external_prices')"):
        plane.append("SELECT ticker, date, close FROM external_prices "
                     "WHERE ticker = ANY($1::text[]) AND date >= $2 AND close IS NOT NULL")
    rows = await pool.fetch(
        " UNION ALL ".join(plane) + " ORDER BY ticker, date", tickers, start)

    closes: dict[str, dict[datetime.date, float]] = {}
    for r in rows:
        closes.setdefault(r["ticker"], {})[r["date"]] = float(r["close"])
    return grid, closes


def _run_bench(start: str, end: str) -> dict:
    """scripts/bench_series.py in the engine venv → {ibov, cdi, offline}.
    Blocking — call via to_thread."""
    proc = enginerun.run_script("bench_series.py", [start, end])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
    return enginerun._last_json(proc.stdout)


async def _benchmarks(grid: list[datetime.date]) -> dict[str, list[float]]:
    """IBOV and CDI rebased onto `grid` (forward-filled, base 100).

    A benchmark is decoration on top of the tenant's own line: if the engine
    subprocess fails or the window is offline, the portfolio index still draws.
    Same posture as /portfolio/price-history's empty-dict fallback.
    """
    if not grid:
        return {}

    # IBOV and CDI over a window are the same numbers for every tenant, and the
    # subprocess that fetches them imports pandas and hits Yahoo/BCB. Cached on
    # (window, today) so it is paid once a day rather than once a page load.
    key = (grid[0], grid[-1], datetime.date.today())
    hit = _BENCH_CACHE.get(key)
    if hit is not None:
        return hit

    try:
        raw = await asyncio.to_thread(
            _run_bench, grid[0].isoformat(), grid[-1].isoformat())
    except Exception:
        return {}          # deliberately not cached — see below
    if raw.get("offline"):
        return {}          # "we did not fetch", not "the benchmark was flat"

    out: dict[str, list[float]] = {}
    for name in ("ibov", "cdi"):
        parsed = {datetime.date.fromisoformat(d): v
                  for d, v in (raw.get(name) or {}).items()}
        if not parsed:
            continue
        # Forward-fill onto our grid: the benchmark calendars (Yahoo sessions,
        # BCB business days) never line up exactly with the plane's.
        series: list[float] = []
        last = 0.0
        for day in grid:
            last = parsed.get(day, last)
            series.append(last)
        out[name] = pidx.rebase(series)
    # Only a real answer is cached. A failed subprocess or an offline window
    # would otherwise pin "no benchmarks" for the rest of the day over what may
    # be a momentary outage — the same posture market_status takes with an
    # unreachable plane.
    _bounded(_BENCH_CACHE)[key] = out
    return out


@portfolio_index_router.get("")
async def get_portfolio_index(
    tickers: str | None = None,
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Daily base-100 index of the tenant's book, with IBOV/CDI overlays.

    The window starts at the tenant's first trade or the plane's first price,
    whichever is later — `coverage.clamped` says which, so the UI can explain a
    chart that starts after the user's real history does.

    `tickers` is a comma-separated subset of `universe`; absent means the whole
    book. Rebuilding the same TWR over a subset is how the user reads which
    holdings carried the return and which dragged on it.
    """
    _user, tid = tenant
    all_deltas, flows, excluded_markets = await _book(session, tid)
    if not all_deltas:
        raise HTTPException(422, "no trades to index — import a portfolio first")

    universe = sorted({t for _, t, _ in all_deltas})
    keep = None if tickers is None else {
        t.strip().upper() for t in tickers.split(",") if t.strip()} & set(universe)
    qty_deltas, flows = pidx.select_tickers(all_deltas, flows, keep)
    if not qty_deltas:
        raise HTTPException(422, "nenhum ativo selecionado tem histórico a indexar")

    # Pinned to the *whole* book, not the selection: toggling an asset has to
    # change the lines and never the x-axis, or the before and after are not
    # comparable — and the grid and benchmark caches would miss on every click.
    # A selection whose first buy lands mid-window simply holds nothing until
    # then, which build_index already draws flat at the base.
    first_trade = min(d for d, _, _ in all_deltas)
    selected = sorted({t for _, t, _ in qty_deltas})
    try:
        grid, closes = await _plane(selected, first_trade)
    except Exception:
        raise HTTPException(503, "market plane unavailable — try again shortly")
    if not grid:
        raise HTTPException(
            422, "the market plane has no prices covering your trade history")

    built = pidx.build_index(grid, closes, qty_deltas, flows)
    bench = await _benchmarks(grid)

    series = [{"date": d, "portfolio": v}
              for d, v in zip(built["dates"], built["index"])]
    for key, values in bench.items():
        for row, v in zip(series, values):
            row[key] = v

    metrics = {"portfolio": pidx.summarize(built["index"], len(grid))}
    for key, values in bench.items():
        metrics[key] = pidx.summarize(values, len(grid))

    return {
        "start": built["dates"][0],
        "end": built["dates"][-1],
        "base": 100.0,
        "series": series,
        "nav": built["nav"][-1],
        "metrics": metrics,
        # What is charted, and everything that could be. Equal when unfiltered.
        "tickers": selected,
        "universe": universe,
        # Surfaced, not hidden (spec/03): everything the index could not see.
        "excluded": {
            "markets": excluded_markets,
            "categories": sorted(pidx.EXCLUDED_CATEGORIES),
            "unpriced_tickers": built["unpriced"],
            "benchmarks": not bench,
        },
        "coverage": {
            "first_trade": first_trade.isoformat(),
            "plane_start": grid[0].isoformat(),
            "clamped": grid[0] > first_trade,
            # Trades newer than the plane's last close are rolled onto that last
            # session and marked there — they are held, but their entry price
            # meets a stale mark. Counted so the UI can say so instead of
            # showing a final point nobody can explain.
            "trades_after_plane": sum(1 for d, _, _ in qty_deltas if d > grid[-1]),
        },
    }
