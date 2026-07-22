"""Rank the latest FII snapshot by yield + discount + quality.

Usage:
    python -m scripts.rank_fii
    python -m scripts.rank_fii --top 30
    python -m scripts.rank_fii --segment Logística
    python -m scripts.rank_fii --weights yield=0.6,discount=0.2,quality=0.2
    python -m scripts.rank_fii --held       # only rank FIIs you currently hold
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from methodologies import fii_filters, fii_mixed
from scripts._common import parse_weights

ROOT = Path(__file__).resolve().parent.parent
FII_SNAPSHOTS = ROOT / "data" / "fii_snapshots"
POSITIONS = ROOT / "data" / "positions.csv"


def latest_snapshot() -> Path:
    files = sorted(FII_SNAPSHOTS.glob("*.csv"))
    if not files:
        raise SystemExit(
            "No FII snapshots in data/fii_snapshots/. "
            "Run fetch_fundamentus_fii.py first."
        )
    return files[-1]


def held_fii_tickers(snapshot: pd.DataFrame) -> set[str]:
    """Held tickers that actually appear in the FII snapshot.

    The earlier "ends in 11" heuristic was leaky: it picked up units like
    SAPR11 (Sanepar's PN+ON unit bundle) which trade as utilities, not FIIs.
    Cross-referencing against the FII screener's `papel` column is the
    authoritative test.
    """
    if not POSITIONS.exists():
        return set()
    pos = pd.read_csv(POSITIONS)
    held = set(pos["ticker"].str.upper())
    fii_universe = set(snapshot["papel"].astype(str).str.upper())
    return held & fii_universe


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--weights", type=str,
                   help="e.g. yield=0.5,discount=0.3,quality=0.2")
    p.add_argument("--segment", type=str,
                   help="filter to a single segment (e.g. 'Logística', 'Shoppings')")
    p.add_argument("--held", action="store_true",
                   help="only rank FIIs in data/positions.csv")
    p.add_argument("--snapshot", type=str,
                   help="path to specific FII snapshot CSV (default: latest)")
    args = p.parse_args(argv[1:])

    snap = Path(args.snapshot) if args.snapshot else latest_snapshot()
    df = pd.read_csv(snap)
    print(f"Snapshot: {snap.name} ({len(df)} FIIs)", file=sys.stderr)

    df = fii_filters.apply(df, segment=args.segment)
    print(f"After hard filters: {len(df)} FIIs", file=sys.stderr)

    if args.held:
        held = held_fii_tickers(df)
        if not held:
            print("Warning: --held requested but no FII positions found", file=sys.stderr)
        else:
            df = df[df["papel"].isin(held)]
            print(f"After --held filter: {len(df)} FIIs", file=sys.stderr)

    if df.empty:
        print("No FIIs left after filters.", file=sys.stderr)
        return 1

    weights = parse_weights(args.weights) if args.weights else None
    ranked = fii_mixed.score(df, weights=weights)

    cols = [
        "papel", "segmento", "cotacao", "score",
        "yield", "discount", "quality",
        "dy", "ffo_yield", "pvp",
        "vacancia", "qtd_imoveis", "liquidez", "dy_ffo_gap",
    ]
    cols = [c for c in cols if c in ranked.columns]
    top = ranked.head(args.top)[cols].copy()

    # Round numeric columns. Show DY / vacância as percentages for readability.
    for c in top.columns:
        if c in ("dy", "ffo_yield", "vacancia", "dy_ffo_gap"):
            top[c] = (top[c] * 100).round(2)
        elif pd.api.types.is_numeric_dtype(top[c]):
            top[c] = top[c].round(3)

    # Column header hint that those columns are percentages.
    rename = {
        "dy": "dy %", "ffo_yield": "ffo %", "vacancia": "vac %",
        "dy_ffo_gap": "dy-ffo %",
    }
    top = top.rename(columns={k: v for k, v in rename.items() if k in top.columns})

    print(tabulate(top, headers="keys", tablefmt="github", showindex=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
