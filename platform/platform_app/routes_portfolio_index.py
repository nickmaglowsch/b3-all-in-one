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
    """
    trades = (await session.scalars(
        select(Trade).where(Trade.tenant_id == tid)
    )).all()
    events = (await session.scalars(
        select(CashEvent).where(CashEvent.tenant_id == tid)
    )).all()

    qty_deltas: list[tuple[datetime.date, str, float]] = []
    flows: list[tuple[datetime.date, str, float]] = []
    excluded_markets: set[str] = set()

    for t in trades:
        if not t.date or not t.ticker:
            continue
        if t.market in pidx.EXCLUDED_MARKETS:
            excluded_markets.add(t.market)
            continue
        if t.signed_qty:
            qty_deltas.append(
                (t.date, pidx.canonical(t.ticker, t.market), float(t.signed_qty)))
        if t.amount is not None and t.side in ("Compra", "Venda"):
            kind = "buy" if t.side == "Compra" else "sell"
            flows.append((t.date, kind, abs(float(t.amount))))

    for e in events:
        if not e.date:
            continue
        if e.event_category == "qty_event" and e.ticker and e.qty:
            qty_deltas.append((e.date, pidx.canonical(e.ticker), float(e.qty)))
        elif e.event_category in pidx.INCOME_CATEGORIES and e.amount is not None:
            flows.append((e.date, "income", abs(float(e.amount))))

    return qty_deltas, flows, sorted(excluded_markets)


async def _plane(tickers: list[str], start: datetime.date) -> tuple[list, dict]:
    """(grid, closes) from the market plane: its trading calendar from `start`,
    and raw closes per requested ticker.

    The grid is every date the plane has any price for — the exchange calendar
    as the plane knows it, so the index has a point on exactly the days a mark
    exists. Raises so the caller can turn an unreachable plane into a 503.
    """
    pool = await get_engine_pool()
    days = await pool.fetch(
        "SELECT DISTINCT date FROM prices WHERE date >= $1 ORDER BY date", start)
    rows = await pool.fetch(
        "SELECT ticker, date, close FROM prices "
        "WHERE ticker = ANY($1::text[]) AND date >= $2 AND close IS NOT NULL "
        "ORDER BY ticker, date",
        tickers, start,
    )

    closes: dict[str, dict[datetime.date, float]] = {}
    for r in rows:
        closes.setdefault(r["ticker"], {})[r["date"]] = float(r["close"])
    return [d["date"] for d in days], closes


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
    try:
        raw = await asyncio.to_thread(
            _run_bench, grid[0].isoformat(), grid[-1].isoformat())
    except Exception:
        return {}
    if raw.get("offline"):
        return {}

    out: dict[str, list[float]] = {}
    for key in ("ibov", "cdi"):
        parsed = {datetime.date.fromisoformat(d): v
                  for d, v in (raw.get(key) or {}).items()}
        if not parsed:
            continue
        # Forward-fill onto our grid: the benchmark calendars (Yahoo sessions,
        # BCB business days) never line up exactly with the plane's.
        series: list[float] = []
        last = 0.0
        for day in grid:
            last = parsed.get(day, last)
            series.append(last)
        out[key] = pidx.rebase(series)
    return out


@portfolio_index_router.get("")
async def get_portfolio_index(
    tenant=Depends(get_current_tenant),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Daily base-100 index of the tenant's book, with IBOV/CDI overlays.

    The window starts at the tenant's first trade or the plane's first price,
    whichever is later — `coverage.clamped` says which, so the UI can explain a
    chart that starts after the user's real history does.
    """
    _user, tid = tenant
    qty_deltas, flows, excluded_markets = await _book(session, tid)
    if not qty_deltas:
        raise HTTPException(422, "no trades to index — import a portfolio first")

    first_trade = min(d for d, _, _ in qty_deltas)
    tickers = sorted({t for _, t, _ in qty_deltas})
    try:
        grid, closes = await _plane(tickers, first_trade)
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
