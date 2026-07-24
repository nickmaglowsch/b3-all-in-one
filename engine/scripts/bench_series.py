"""Print daily IBOV and CDI levels over a window as JSON (one line to stdout).

The portfolio index (platform /portfolio/index) charts the tenant's book against
IBOV and CDI. Both series are the engine's business — it already owns the Yahoo
and BCB fetchers plus their disk cache — so the platform shells this instead of
re-implementing two downloaders in its own venv. Mirrors list_signals.py.

Usage:
    python scripts/bench_series.py <start> <end>        # ISO dates

Output: {"ibov": {"YYYY-MM-DD": level, ...}, "cdi": {...}, "offline": bool}.
Levels are raw (IBOV points, CDI a compounded accrual factor) — the caller
rebases both onto its own trading-day grid, which is the only place that knows
the tenant's window.

Network: IBOV comes from Yahoo (^BVSP, a total-return index by construction) and
CDI from the Brazilian Central Bank. With ENGINE_BENCH_OFFLINE=1 this fetches
nothing and returns empty series with offline=true — the caller then reports
benchmarks as unavailable rather than drawing a flat line the user would read as
"IBOV was up 0%". Same env flag the bench runners use.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    start, end = sys.argv[1], sys.argv[2]

    if os.environ.get("ENGINE_BENCH_OFFLINE") == "1":
        json.dump({"ibov": {}, "cdi": {}, "offline": True}, sys.stdout)
        sys.stdout.write("\n")
        return

    from backtests.core.data import download_benchmark, download_cdi_daily

    ibov = download_benchmark("^BVSP", start, end)
    # download_cdi_daily yields daily *rates* (0.0001 == 0.01%); compound them
    # into a level series so both benchmarks rebase the same way.
    cdi = (1.0 + download_cdi_daily(start, end)).cumprod()

    out = {
        "ibov": {d.strftime("%Y-%m-%d"): float(v)
                 for d, v in ibov.dropna().items()},
        "cdi": {d.strftime("%Y-%m-%d"): float(v)
                for d, v in cdi.dropna().items()},
        "offline": False,
    }
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
