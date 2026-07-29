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
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import enginerun, portfolio_index as pidx, subprocs
from platform_app.db import get_async_session, get_engine_pool
from platform_app.models import CashEvent, RendaFixaMark, RendaFixaTerms, Trade
from platform_app.tenancy import get_current_tenant

portfolio_index_router = APIRouter(prefix="/portfolio/index", tags=["portfolio-index"])


# Why a renda fixa contract the tenant holds is not on the chart. Phrased for
# the caveat block the UI already renders, since the endpoint's other user-facing
# strings are pt-BR too.
# The `code` travels beside the prose so the UI can branch on it — only the
# "no indexer" case has a form that fixes it, and selecting those by
# substring-matching the sentence means a copy edit silently removes the form.
_RF_STILL_HELD = ("sem_indexador",
                  "ainda em carteira e sem um segundo preço — envie o Relatório "
                  "Consolidado Mensal da B3, que traz o preço atualizado do "
                  "contrato, ou informe o indexador aqui")
_RF_FROZEN = ("custodia_bloqueada", "retirada de custódia sem liquidação")
_RF_AMORTIZED = ("amortizacao", "amortização em vida não modelada")
_RF_NO_SERIES = ("sem_indice", "índice do BCB indisponível para o período")


def _renda_fixa(trades, events, terms: dict,
                plane_priced: frozenset[str] = frozenset(),
                report_marks=(),
                ) -> tuple[dict, dict[str, str]]:
    """(marks, blocked) for the renda fixa contracts in a book.

    `marks` is what `pidx.accrue_rf` interpolates between: the per-unit price of
    every aplicação and every resgate B3 reported. `blocked` names the contracts
    that must stay off the chart even when they have two marks, and why.

    Two blockers, both cases where the marks would lie about the path:
      - a custody withdrawal that never settled (issuer under intervention) —
        the paper is still held, at a value nobody has published;
      - principal returned mid-life via amortização, which a CRI or CRA pays in
        instalments. taxcore reduces the *cost* on those and leaves the quantity
        alone, so the per-unit price has to step down with each one; accruing
        straight through would over-value the position by the returned principal
        for the rest of its life.

    `plane_priced` names the contracts the market plane carries a real daily
    price for — Tesouro Direto titles, published by the issuer and stored by
    `engine/b3_pipeline/tesouro_direto.py`. Those are left out of `marks`
    entirely rather than accrued: the caller unions them into `rf_priced` so they
    are admitted, and then prices them off the plane like any listed holding. An
    accrual would otherwise be built from the tenant's two fills and used to
    *overwrite* the issuer's own numbers in `closes`.

    `report_marks` are (ticker, date, price) rows read off the tenant's posição
    reports — the per-unit `Preço Atualizado` B3 publishes for paper still in
    custody. They are what makes an unredeemed contract drawable without an
    indexer: a fill and a report price are two dated marks like any other pair.
    Merged only into contracts the book already bought, because a mark with no
    aplicação behind it prices nothing and would only add a holding the chart
    then has to disclose as unpriceable. The blockers below still apply — B3
    keeps printing an Atualizado for frozen and amortized paper too, and those
    are exactly the numbers the blockers exist to refuse.
    """
    marks: dict[str, list[tuple[datetime.date, float]]] = {}
    blocked: dict[str, str] = {}

    for t_date, ticker, market, _sq, _amount, _side, price in trades:
        if (t_date and ticker and market == pidx.RENDA_FIXA_MARKET and price
                and ticker not in plane_priced):
            marks.setdefault(ticker, []).append((t_date, float(price)))

    for ticker, m_date, price in report_marks:
        if m_date and price and ticker in marks:
            marks[ticker].append((m_date, float(price)))

    for _d, category, ticker, _qty, _amount in events:
        if not ticker or ticker not in marks:
            continue
        if category == "frozen":
            blocked[ticker] = _RF_FROZEN
        elif category == "amortization":
            blocked.setdefault(ticker, _RF_AMORTIZED)

    for ticker, points in marks.items():
        if len(points) < 2 and ticker not in blocked and ticker not in terms:
            blocked[ticker] = _RF_STILL_HELD

    return {t: p for t, p in marks.items() if t not in blocked}, blocked


async def _plane_priced(contracts: set[str]) -> frozenset[str]:
    """Of `contracts`, the ones the market plane can price on its own.

    Tesouro Direto titles: the issuer publishes a PU for every one of them every
    business day, which the engine stores in `external_prices` under the same
    name B3 writes in the extract (engine/b3_pipeline/tesouro_direto.py). Such a
    contract needs neither a second mark nor an indexer the tenant typed in — it
    is priced like any listed holding.

    Guarded on the table's existence for the same reason `_plane` is: the engine
    creates it on its next pipeline run, and the two deploy independently.
    """
    if not contracts:
        return frozenset()
    pool = await get_engine_pool()
    if not await pool.fetchval("SELECT to_regclass('external_prices')"):
        return frozenset()
    rows = await pool.fetch(
        "SELECT DISTINCT ticker FROM external_prices WHERE ticker = ANY($1::text[])",
        sorted(contracts))
    return frozenset(r["ticker"] for r in rows)


async def _book(session: AsyncSession, tid):
    """(qty_deltas, flows, excluded_markets, rf_marks, rf_terms, rf_blocked).

    Trades carry `signed_qty` (buys +, sells −) and qty_events carry an already
    signed `qty` — both set by taxcore's load_b3, so no side parsing here.

    Flows are 4-tuples here, (date, kind, amount, ticker): the ticker is what
    `pidx.select_tickers` needs to keep an excluded asset's cash out of a
    filtered book. It strips the field back off before `build_index` sees it.

    Columns, not entities: this reads seven fields off trades and five off
    cash_events, and loading whole ORM instances for a multi-year book built an
    object per row to throw all but those away.

    The renda fixa contracts come back as `rf_marks` rather than as a finished
    price series because the grid they are drawn on starts at the first trade,
    which is not known until this function has run. Whether a contract is
    *priceable* does not depend on that grid — two marks imply a rate wherever
    the window lands — so the admission decision can be made here and the
    drawing left to the caller.
    """
    trades = (await session.execute(
        select(Trade.date, Trade.ticker, Trade.market,
               Trade.signed_qty, Trade.amount, Trade.side, Trade.price)
        .where(Trade.tenant_id == tid)
    )).all()
    events = (await session.execute(
        select(CashEvent.date, CashEvent.event_category, CashEvent.ticker,
               CashEvent.qty, CashEvent.amount)
        .where(CashEvent.tenant_id == tid)
    )).all()

    term_rows = (await session.execute(
        select(RendaFixaTerms.ticker, RendaFixaTerms.indexer,
               RendaFixaTerms.rate_pct)
        .where(RendaFixaTerms.tenant_id == tid)
    )).all()
    rf_terms = {t: (ix, float(rate)) for t, ix, rate in term_rows}

    # The prices B3 published for contracts still in custody, one per posição
    # report the tenant uploaded. These are what let an unredeemed contract be
    # drawn without an indexer — see `_renda_fixa`.
    report_marks = (await session.execute(
        select(RendaFixaMark.ticker, RendaFixaMark.date, RendaFixaMark.price)
        .where(RendaFixaMark.tenant_id == tid)
    )).all()

    # The contracts, as opposed to the listed fixed-income ETFs that share their
    # market label — `is_excluded` with no pricing knowledge is exactly that
    # split, so the ticker-shape rule is not written down a second time here.
    contracts = {t for _d, t, m, _sq, _a, _s, _p in trades
                 if t and m == pidx.RENDA_FIXA_MARKET and pidx.is_excluded(t, m)}
    try:
        plane_priced = await _plane_priced(contracts)
    except Exception:                       # noqa: BLE001
        # An unreachable engine is `_plane`'s 503 a moment later, not a 500 here.
        plane_priced = frozenset()

    rf_marks, rf_blocked = _renda_fixa(
        trades, events, rf_terms, plane_priced, report_marks)
    rf_priced = frozenset(rf_marks) | plane_priced

    qty_deltas: list[tuple[datetime.date, str, float]] = []
    flows: list[tuple[datetime.date, str, float, str | None]] = []
    excluded_markets: set[str] = set()

    for t_date, ticker, market, signed_qty, amount, side, _price in trades:
        if not t_date or not ticker:
            continue
        if pidx.is_excluded(ticker, market, rf_priced):
            # Renda fixa is named per contract in `rf_blocked`, with a reason;
            # rolling it up as a bare market label here would say much less.
            if market != pidx.RENDA_FIXA_MARKET:
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

    return (qty_deltas, flows, sorted(excluded_markets),
            rf_marks, rf_terms, rf_blocked)


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


async def current_rf_marks(
    session: AsyncSession, tid: uuid.UUID
) -> dict[str, tuple[float, datetime.date]]:
    """Each renda fixa contract's per-unit price today, and the day it is for.

    B3 marks a CDB once a month, in the posição report. Left at that number a
    contract sits flat for up to a month and then jumps — and /portfolio/index
    on the same screen does not, because `accrue_rf` already grows it from the
    indexer the tenant entered. Two numbers for one book. This is that same
    accrual read at its last point, so the table and the chart agree by
    construction rather than by two implementations staying in step.

    Without terms there is no slope to model, so the contract keeps the mark B3
    published and the month-end date that dates it — the previous behaviour, and
    still the honest one.
    """
    marks: dict[str, list[tuple[datetime.date, float]]] = {}
    for ticker, day, price in (await session.execute(
        select(RendaFixaMark.ticker, RendaFixaMark.date, RendaFixaMark.price)
        .where(RendaFixaMark.tenant_id == tid)
    )).all():
        if price:
            marks.setdefault(ticker, []).append((day, float(price)))
    if not marks:
        return {}
    latest = {t: max(pts) for t, pts in marks.items()}
    reported = {t: (pu, day) for t, (day, pu) in latest.items()}

    terms = {t: (ix, float(rate)) for t, ix, rate in (await session.execute(
        select(RendaFixaTerms.ticker, RendaFixaTerms.indexer, RendaFixaTerms.rate_pct)
        .where(RendaFixaTerms.tenant_id == tid)
    )).all() if t in marks}
    if not terms:
        return reported       # nothing to accrue → no plane round-trip at all

    try:
        grid, closes = await _plane(
            sorted({s for s in (pidx.rf_series_for(ix) for ix, _ in terms.values()) if s}),
            min(day for day, _ in latest.values()))
    except Exception:         # noqa: BLE001 — same posture as /positions itself
        return reported       # plane down → the reported mark, not a 500
    if not grid:
        return reported

    accrued = pidx.accrue_rf(grid, marks, terms, closes)
    for ticker, series in accrued.items():
        day = max(series)
        # Only ever move a mark forward in time. A contract whose last posição
        # report is newer than the plane's last session (B3 publishes month-end
        # before the exchange's next close) would otherwise be walked backwards.
        if day >= reported[ticker][1]:
            reported[ticker] = (series[day], day)
    return reported


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
    return subprocs.last_json(proc.stdout, strict=True)


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
    (all_deltas, flows, excluded_markets,
     rf_marks, rf_terms, rf_blocked) = await _book(session, tid)
    if not all_deltas:
        raise HTTPException(422, "no trades to index — import a portfolio first")

    # Pinned to the *whole* book, not the selection: toggling an asset has to
    # change the lines and never the x-axis, or the before and after are not
    # comparable — and the grid and benchmark caches would miss on every click.
    # A selection whose first buy lands mid-window simply holds nothing until
    # then, which build_index already draws flat at the base.
    first_trade = min(d for d, _, _ in all_deltas)
    candidates = sorted({t for _, t, _ in all_deltas})
    # The BCB index levels ride on the plane under reserved tickers, so they
    # come back from the same query as everything else — asked for only when a
    # contract in this book actually needs one.
    wanted = {pidx.rf_series_for(ix) for ix, _ in rf_terms.values()}
    try:
        grid, closes = await _plane(
            candidates + sorted(w for w in wanted if w), first_trade)
    except Exception:
        raise HTTPException(503, "market plane unavailable — try again shortly")
    if not grid:
        raise HTTPException(
            422, "the market plane has no prices covering your trade history")

    levels = {name: closes.pop(name) for name in list(closes)
              if name in pidx.RF_SERIES_TICKERS}

    # Renda fixa is priced off the tenant's own extract rather than the plane,
    # so it is accrued here rather than unioned into the SQL above.
    #
    # This runs *before* the book is assembled, and that ordering is the whole
    # point. `_book` can only tell which contracts are priceable in principle —
    # whether a series exists to price them against needs the grid, which needs
    # the first trade, which needs `_book`. So a contract can be admitted there
    # and refused here, and a refused contract left in the book is not merely
    # missing from the NAV: its aplicação still counts as an inflow, so the
    # chain divides a NAV that never rose by a denominator that did and books
    # the whole purchase as a same-day total loss that never recovers. The
    # disclosure below is a caveat, not a guard — it would have narrated that
    # wrong number instead of removing it.
    accrued = pidx.accrue_rf(grid, rf_marks, rf_terms, levels)
    undrawn = set(rf_marks) - set(accrued)
    if undrawn:
        for ticker in undrawn:
            rf_blocked.setdefault(ticker, _RF_NO_SERIES)
        # Dropped here rather than via `keep` below, because `select_tickers`
        # reads a non-None `keep` as "the user filtered the book" and discards
        # cash events that name no ticker. This is not a user filter.
        all_deltas = [d for d in all_deltas if d[1] not in undrawn]
        flows = [f for f in flows if f[3] not in undrawn]
        if not all_deltas:
            raise HTTPException(422, "no trades to index — import a portfolio first")

    universe = sorted({t for _, t, _ in all_deltas})
    keep = None if tickers is None else {
        t.strip().upper() for t in tickers.split(",") if t.strip()} & set(universe)
    qty_deltas, flows = pidx.select_tickers(all_deltas, flows, keep)
    if not qty_deltas:
        raise HTTPException(422, "nenhum ativo selecionado tem histórico a indexar")
    selected = sorted({t for _, t, _ in qty_deltas})

    # Restricted to `selected` for the same reason every other series is: the
    # holding filter rebuilds the whole TWR, it does not slice a finished line.
    closes.update({t: s for t, s in accrued.items() if t in selected})

    built = pidx.build_index(grid, closes, qty_deltas, flows)
    bench = await _benchmarks(grid)

    series = [{"date": d, "portfolio": v}
              for d, v in zip(built["dates"], built["index"])]
    for key, values in bench.items():
        for row, v in zip(series, values):
            row[key] = v

    span = pidx.years_between(grid[0], grid[-1])
    metrics = {"portfolio": pidx.summarize(built["index"], span)}
    for key, values in bench.items():
        metrics[key] = pidx.summarize(values, span)

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
            # Named one by one with a reason, not rolled up as a market label:
            # a contract left off the chart is a holding the user can act on
            # (send a longer extract, wait for the resgate), and "Renda Fixa"
            # alone says none of that.
            # Scoped to the current selection like every other entry in this
            # block: a caveat naming contracts the line above does not contain
            # describes a different chart than the one on screen.
            "renda_fixa": [{"ticker": t, "code": c, "reason": r}
                           for t, (c, r) in sorted(rf_blocked.items())
                           if keep is None or t in keep],
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
