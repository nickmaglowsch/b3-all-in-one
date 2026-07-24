"""Rank the latest snapshot by a chosen methodology.

Usage:
    python -m scripts.rank                       # mixed methodology, top 20
    python -m scripts.rank --top 30
    python -m scripts.rank --weights value=0.6,quality=0.4,growth=0
    python -m scripts.rank --watchlist            # only rank tickers in data/watchlist.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from methodologies import filters, mixed
from scripts._common import SNAPSHOTS, latest_csv_path, parse_weights

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "data" / "watchlist.txt"


def load_watchlist() -> set[str]:
    if not WATCHLIST.exists():
        return set()
    return {
        line.strip().upper()
        for line in WATCHLIST.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--weights", type=str, help="e.g. value=0.5,quality=0.3,growth=0.2")
    p.add_argument("--watchlist", action="store_true", help="filter to watchlist only")
    p.add_argument("--allow-units", action="store_true", help="don't exclude tickers ending in 11")
    p.add_argument("--snapshot", type=str, help="path to specific snapshot CSV (default: latest)")
    args = p.parse_args(argv[1:])

    snap = Path(args.snapshot) if args.snapshot else latest_snapshot()
    df = pd.read_csv(snap)
    print(f"Snapshot: {snap.name} ({len(df)} tickers)", file=sys.stderr)

    df = filters.apply(df, allow_units=args.allow_units)
    print(f"After hard filters: {len(df)} tickers", file=sys.stderr)

    if args.watchlist:
        wl = load_watchlist()
        if not wl:
            print("Warning: --watchlist requested but data/watchlist.txt is empty", file=sys.stderr)
        else:
            df = df[df["papel"].isin(wl)]
            print(f"After watchlist filter: {len(df)} tickers", file=sys.stderr)

    weights = parse_weights(args.weights) if args.weights else None
    ranked = mixed.score(df, weights=weights)

    cols = [
        "papel", "cotacao", "score", "value", "quality", "growth",
        "pl", "pvp", "evebit", "roic", "roe", "dy", "c5y", "divbpatr",
    ]
    cols = [c for c in cols if c in ranked.columns]
    top = ranked.head(args.top)[cols].copy()

    # Pretty-print: round numeric columns
    for c in top.columns:
        if pd.api.types.is_numeric_dtype(top[c]):
            top[c] = top[c].round(3)

    print(tabulate(top, headers="keys", tablefmt="github", showindex=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
