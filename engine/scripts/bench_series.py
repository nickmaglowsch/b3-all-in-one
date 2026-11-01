"""Print daily benchmark levels over a window as JSON (one line to stdout).

Two callers, one reader. The portfolio index (platform /portfolio/index) charts
the tenant's book against IBOV and CDI; the backtest bench (platform
/benchmarks) charts a run against whichever ruler the user picked. Both are the
engine's business — it owns the plane the series are stored in — so the platform
shells this instead of re-implementing a reader in its own venv.

Usage:
    python scripts/bench_series.py <start> <end> [name,name,...]   # ISO dates

`names` defaults to `ibov,cdi`, which is the portfolio index's call and the
contract it has always had. Output: {"<name>": {"YYYY-MM-DD": level, ...}, ...,
"offline": bool} — one key per *requested* name, and nothing else.

Levels are raw and each series is on its own scale: IBOV and the ANBIMA indexes
in points, CDI, IPCA and Selic as compounded accrual factors, S&P 500 and bitcoin
as prices in reais. Nothing here rebases them, because only the caller knows the
window's own grid — and mixing scales is exactly why the caller must rebase
before drawing.

Network: none. Everything comes out of the plane the ingest runs write, so a
page load reads the database and nothing else. `offline` therefore means "the
plane holds none of the requested series for this window", and the caller reports
those benchmarks as unavailable rather than drawing a flat line the user would
read as "the market was up 0%".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# name -> (symbol, market). The names are the API's contract: the platform route
# and the bench UI both spell them, so treat a rename as a breaking change.
#
# The `$` symbols are stored index levels in `external_prices` and belong to no
# market — download_benchmark routes them to download_index_level and ignores the
# `market` argument, so B3 there is inert, not a claim. Bitcoin is the one real
# ticker in the list, and it is read through CRYPTO precisely so the projection
# is `adj_close_brl`: the numeraire is reais for every market on this plane, and
# a ruler quoted in USDT would credit the dollar's move to the strategy.
BENCHMARKS = {
    "ibov": ("$IBOV", "B3"),        # B3's published index, points
    "cdi": ("$CDI", "B3"),          # BCB SGS 12, cumulative factor
    "ipca": ("$IPCA", "B3"),        # BCB SGS 433, cumulative factor
    "sp500": ("$SPX", "B3"),        # SPY total return in BRL (a named proxy)
    "bitcoin": ("BTCUSDT", "CRYPTO"),
    "selic": ("$SELIC", "B3"),      # BCB SGS 11, cumulative factor
    # ANBIMA's fixed-income indexes (b3_pipeline/anbima_series.py), index
    # points. The P2 pair is stored going forward only, so an older window
    # comes back empty for them and the caller reports them unavailable.
    "irfm1": ("$IRFM1", "B3"),
    "irfm1p": ("$IRFM1P", "B3"),
    "irfm": ("$IRFM", "B3"),
    "irfmp2": ("$IRFMP2", "B3"),
    "imab5": ("$IMAB5", "B3"),
    "imab5p": ("$IMAB5P", "B3"),
    "imab5p2": ("$IMAB5P2", "B3"),
    "imab": ("$IMAB", "B3"),
    "imas": ("$IMAS", "B3"),
    "imageral": ("$IMAGERAL", "B3"),
}

DEFAULT_NAMES = ("ibov", "cdi")


def series(start: str, end: str, names=DEFAULT_NAMES) -> dict:
    """Requested benchmarks over [start, end], as {name: {date: level}}.

    An unknown name raises KeyError rather than answering an empty series:
    empty means "the plane has no rows yet" and a typo means "this chart will
    silently lose a line", and the caller has to be able to tell them apart.
    """
    from backtests.core.data import download_benchmark

    out: dict = {}
    for name in names:
        symbol, market = BENCHMARKS[name]
        level = download_benchmark(symbol, start, end, market=market)
        out[name] = {d.strftime("%Y-%m-%d"): float(v) for d, v in level.items()}
    # Per-series, not all-or-nothing: each ruler is written by a different
    # pipeline step, so one being late must not blank the others.
    out["offline"] = not any(out[name] for name in names)
    return out


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    names = args[2].split(",") if len(args) > 2 and args[2] else DEFAULT_NAMES
    json.dump(series(args[0], args[1], names), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
