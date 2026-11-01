"""Rulers for the backtest bench: the market series a run is measured against.

The compare panel could only put a backtest next to another backtest, which
answers "which of my ideas was better" and never "was either of them worth the
trouble". The second question needs a ruler the user did not write — the CDI a
savings account would have paid, the IBOV, the IPCA that says what the money
bought — and those are already on the plane: the same rows /portfolio/index
charts a real book against.

So this module is thin on purpose. It shells the engine's one reader
(scripts/bench_series.py) and hands back raw levels, one series per requested
name, exactly as stored. It deliberately does NOT rebase: every series is on its
own scale (IBOV in points, CDI and IPCA as compounded factors, S&P 500 and
bitcoin as prices in reais), and the only place that knows which grid the lines
share is the caller drawing them.

Registered in main.py as `benchmarks_router`, gated on the `bench` module —
a benchmark is only reachable from the bench, and the engine subprocess behind
it is not something an unentitled account should be able to spend.
"""
from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from platform_app import enginerun
from platform_app.routes_portfolio_index import bounded
from platform_app.tenancy import get_current_tenant

benchmarks_router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

# The picker's own order — cheapest ruler first, then the two indices a Brazilian
# investor is actually benchmarked against, then the two that carry the dollar.
#
# `key` is the engine's name (scripts/bench_series.BENCHMARKS) and travels all
# the way to the UI, so a rename is a breaking change in three places at once.
# `note` is not decoration: each of these is a different kind of number, and a
# chart that overlays a fund proxy, an inflation index and a coin without saying
# so invites exactly the comparison the product exists to make honest.
CATALOG = [
    {"key": "cdi", "label": "CDI",
     "note": "juros diários acumulados (BCB) — o custo de oportunidade sem risco"},
    {"key": "ipca", "label": "IPCA",
     "note": "inflação acumulada (BCB) — bater isso é ganhar poder de compra"},
    {"key": "ibov", "label": "IBOVESPA",
     "note": "índice à vista da B3, em pontos — sem os proventos das ações"},
    {"key": "sp500", "label": "S&P 500 (em reais)",
     "note": "retorno total do SPY convertido a reais — carrega o dólar junto"},
    {"key": "bitcoin", "label": "Bitcoin (em reais)",
     "note": "BTC/USDT convertido a reais — carrega o dólar junto"},
    # Appended rather than slotted by kind: the bench colours a ruler by its
    # position here, and moving the five above would recolour saved views.
    {"key": "selic", "label": "Selic",
     "note": "taxa Selic diária acumulada (BCB) — o que paga um Tesouro Selic, "
             "colado no CDI"},
    # ANBIMA's indexes (engine/b3_pipeline/anbima_series.py). None is
    # investable: each is a portfolio of títulos públicos marked to market, and
    # an ETF that tracks one earns it minus its fee.
    {"key": "irfm1", "label": "IRF-M 1",
     "note": "prefixados (LTN/NTN-F) até 1 ano, a mercado (ANBIMA) — não investível"},
    {"key": "irfm1p", "label": "IRF-M 1+",
     "note": "prefixados acima de 1 ano, a mercado (ANBIMA) — não investível"},
    {"key": "irfm", "label": "IRF-M",
     "note": "todos os prefixados, a mercado (ANBIMA) — não investível"},
    {"key": "irfmp2", "label": "IRF-M P2",
     "note": "seleção de prefixados que o IRFM11 replica (ANBIMA) — não "
             "investível; guardado só desde que o ingest começou, a ANBIMA não publica "
             "o histórico"},
    {"key": "imab5", "label": "IMA-B 5",
     "note": "NTN-B (IPCA+) até 5 anos, a mercado (ANBIMA) — não investível"},
    {"key": "imab5p", "label": "IMA-B 5+",
     "note": "NTN-B acima de 5 anos, a mercado (ANBIMA) — não investível"},
    {"key": "imab5p2", "label": "IMA-B 5 P2",
     "note": "seleção de NTN-B até 5 anos que o B5P211 replica (ANBIMA) — não "
             "investível; guardado só desde que o ingest começou, a ANBIMA não publica "
             "o histórico"},
    {"key": "imab", "label": "IMA-B",
     "note": "todas as NTN-B (IPCA+), a mercado (ANBIMA) — não investível"},
    {"key": "imas", "label": "IMA-S",
     "note": "todas as LFT (Tesouro Selic), a mercado (ANBIMA) — não investível"},
    {"key": "imageral", "label": "IMA-Geral",
     "note": "todos os títulos públicos federais, a mercado (ANBIMA) — não "
             "investível"},
]

KEYS = [b["key"] for b in CATALOG]

# The plane starts in 2010 (b3_pipeline/index_series.INDEX_SERIES_START) and a
# bare call should be answerable without the caller inventing a default.
DEFAULT_START = datetime.date(2010, 1, 1)

# Process-lifetime cache, same lifecycle and bound as the portfolio index's
# overlay cache. These levels are the same numbers for every tenant and change
# once a day, and every open compare panel asks for them.
_CACHE: dict[tuple, dict[str, dict[str, float]]] = {}
_CACHE_MAX = 256


def clear_cache() -> None:
    """Drop the memo. For tests — nothing in the request path needs it."""
    _CACHE.clear()


def _run_bench(start: str, end: str, names: list[str]) -> dict:
    """scripts/bench_series.py in the engine venv → {name: {date: level}, ...}.
    Blocking — call via to_thread."""
    return enginerun.run_json("bench_series.py", [start, end, ",".join(names)])


def _parse_keys(keys: str | None) -> list[str]:
    """The requested names, in the catalogue's order rather than the caller's.

    An unknown name is a 422 and not an empty series: empty means "the plane has
    no rows for this window yet", a typo means "this line will silently never
    appear", and a caller cannot tell those apart from the payload alone.
    """
    if not keys:
        return list(KEYS)
    asked = [k.strip() for k in keys.split(",") if k.strip()]
    unknown = [k for k in asked if k not in KEYS]
    if unknown:
        raise HTTPException(422, f"unknown benchmark(s): {', '.join(unknown)}")
    return [k for k in KEYS if k in asked]


@benchmarks_router.get("")
async def list_benchmarks(_tenant=Depends(get_current_tenant)) -> list[dict]:
    """The rulers the bench can draw. Static — the catalogue is code, not data."""
    return CATALOG


@benchmarks_router.get("/series")
async def get_benchmark_series(
    start: datetime.date = Query(DEFAULT_START),
    end: datetime.date | None = Query(None),
    keys: str | None = Query(None),
    _tenant=Depends(get_current_tenant),
) -> dict:
    """Raw daily levels for the requested benchmarks over [start, end].

    `unavailable` names every requested benchmark that came back with nothing —
    a series dropped in silence is indistinguishable from one the user never
    selected, and the page has to be able to say which happened. An engine
    failure lands there too: a ruler is decoration on top of the runs the user
    picked, and the overlay still draws without it (the same posture
    /portfolio/index takes with its IBOV/CDI subprocess).
    """
    end = end or datetime.date.today()
    if start > end:
        raise HTTPException(422, "start must not be after end")
    names = _parse_keys(keys)

    series = await _levels(start.isoformat(), end.isoformat(), names)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "series": series,
        "unavailable": [n for n in names if n not in series],
    }


async def _levels(start: str, end: str, names: list[str]) -> dict[str, dict]:
    """Cached {name: {date: level}}, empty series dropped.

    Keyed on the day as well as the window: yesterday's answer for a window
    ending today is short by a session. Only a real answer is cached — pinning a
    momentary engine outage for the rest of the day would report a series the
    plane actually holds as missing until the process restarts.
    """
    key = (start, end, tuple(names), datetime.date.today())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    try:
        raw = await asyncio.to_thread(_run_bench, start, end, names)
    except Exception:
        return {}
    out = {n: raw[n] for n in names if raw.get(n)}
    bounded(_CACHE, _CACHE_MAX)[key] = out
    return out
