"""Print daily IBOV and CDI levels over a window as JSON (one line to stdout).

The portfolio index (platform /portfolio/index) charts the tenant's book against
IBOV and CDI. Both series are the engine's business — it owns the plane they are
stored in — so the platform shells this instead of re-implementing two readers in
its own venv. Mirrors list_signals.py.

Usage:
    python scripts/bench_series.py <start> <end>        # ISO dates

Output: {"ibov": {"YYYY-MM-DD": level, ...}, "cdi": {...}, "offline": bool}.
Levels are raw (BOVA11 in reais, CDI a compounded accrual factor) — the caller
rebases both onto its own trading-day grid, which is the only place that knows
the tenant's window.

Network: none. IBOV comes from BOVA11 in `prices` (COTAHIST) and CDI from `$CDI`
in `external_prices` (BCB, written by the ingest run), so a page load reads the
database and nothing else. `offline` therefore no longer means "we were told not
to fetch" — it means the plane holds neither series for this window, and the
caller reports benchmarks as unavailable rather than drawing a flat line the user
would read as "IBOV was up 0%".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def series(start: str, end: str) -> dict:
    from backtests.core.data import download_benchmark, download_index_level

    ibov = download_benchmark("^BVSP", start, end)
    cdi = download_index_level("$CDI", start, end)

    out = {
        "ibov": {d.strftime("%Y-%m-%d"): float(v) for d, v in ibov.items()},
        "cdi": {d.strftime("%Y-%m-%d"): float(v) for d, v in cdi.items()},
    }
    # Per-series, not all-or-nothing: BOVA11 and $CDI are written by different
    # pipeline steps, so one being late must not blank the other.
    out["offline"] = not out["ibov"] and not out["cdi"]
    return out


def main() -> None:
    json.dump(series(sys.argv[1], sys.argv[2]), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
