"""Suggest how to deploy new capital, weighted by ranking score.

Default behavior: pick the top N names from the current ranking that pass
filters AND are either already in your portfolio or in your watchlist, then
allocate the new capital so that the resulting weights move toward equal-weight
across those top names (a simple anchor — adjust to taste).

This is intentionally simple. It is NOT mean-variance optimization or anything
fancy. It is a starting point for the user to think about, not an order to
execute.

Usage:
    python -m scripts.suggest_allocation --amount 5000
    python -m scripts.suggest_allocation --amount 5000 --top 8
    python -m scripts.suggest_allocation --amount 5000 --new-only   # only buy names not yet held
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from methodologies import filters, mixed
from scripts._common import price_lookup

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "positions.csv"
WATCHLIST = ROOT / "data" / "watchlist.txt"
SNAPSHOTS = ROOT / "data" / "snapshots"


def load_watchlist() -> set[str]:
    if not WATCHLIST.exists():
        return set()
    return {ln.strip().upper() for ln in WATCHLIST.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def load_all_prices() -> dict[str, float]:
    """Ticker→price across equity, FII, and external sources, equity-first.

    Without every source, FII holdings (BTCI11, HGBS11, …) and BDR-ETFs like
    IVVB11 would value at 0 in 'already_held_value' despite being real money.
    """
    prices = price_lookup()
    return dict(zip(prices["ticker"].str.upper(), prices["price"]))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--amount", type=float, required=True, help="New capital in BRL")
    p.add_argument("--top", type=int, default=10, help="How many names to consider")
    p.add_argument("--new-only", action="store_true",
                   help="Only suggest names not already in your portfolio")
    args = p.parse_args(argv[1:])

    snap_files = sorted(SNAPSHOTS.glob("*.csv"))
    if not snap_files:
        print("No snapshots. Run fetch_fundamentus.py first.", file=sys.stderr)
        return 1
    snap = pd.read_csv(snap_files[-1])

    # Price lookup spans equity + FII + external sources. The equity snapshot
    # is still used as the *ranking universe* below — FIIs and BDR-ETFs are
    # not eligible candidates because the mixed methodology and filters expect
    # equity-style metrics (P/L, ROE, EV/EBIT, …). But for valuing already-held
    # positions we need every source.
    all_prices = load_all_prices()

    held: set[str] = set()
    held_qty: dict[str, float] = {}
    held_value: dict[str, float] = {}
    if POSITIONS.exists():
        pos = pd.read_csv(POSITIONS)
        held = set(pos["ticker"].str.upper())
        held_qty = dict(zip(pos["ticker"].str.upper(), pos["qty"]))
        held_value = {t: held_qty[t] * all_prices.get(t, 0.0) for t in held}

    watchlist = load_watchlist()
    universe_tickers = held | watchlist
    if not universe_tickers:
        print("No watchlist or positions. Add tickers to data/watchlist.txt first.", file=sys.stderr)
        return 1

    df = snap[snap["papel"].isin(universe_tickers)]
    df = filters.apply(df)
    ranked = mixed.score(df)

    if args.new_only:
        ranked = ranked[~ranked["papel"].isin(held)]

    candidates = ranked.head(args.top).copy()
    if candidates.empty:
        print("No candidates after filtering. Loosen filters or expand watchlist.", file=sys.stderr)
        return 1

    # Equal-weight target across candidates, anchored on score (higher score = larger slice)
    candidates["target_weight"] = candidates["score"] / candidates["score"].sum()
    candidates["alloc_brl"] = candidates["target_weight"] * args.amount
    candidates["alloc_qty"] = (candidates["alloc_brl"] / candidates["cotacao"]).round(0).astype(int)
    candidates["alloc_actual_brl"] = (candidates["alloc_qty"] * candidates["cotacao"]).round(2)
    candidates["already_held_value"] = candidates["papel"].map(held_value).fillna(0).round(2)

    show = candidates[[
        "papel", "cotacao", "score",
        "already_held_value",
        "target_weight", "alloc_qty", "alloc_actual_brl"
    ]].copy()
    for c in ("cotacao", "score", "target_weight"):
        show[c] = show[c].round(3)

    print(tabulate(show, headers="keys", tablefmt="github", showindex=False))
    deployed = candidates["alloc_actual_brl"].sum()
    print()
    print(f"Capital to deploy:  R$ {args.amount:>10,.2f}")
    print(f"Suggested deploy:   R$ {deployed:>10,.2f}")
    print(f"Leftover (cash):    R$ {args.amount - deployed:>10,.2f}")
    print()
    print("NOTE: This is a starting point, not an order. Cross-check each name "
          "against the latest ranking and your own thesis before buying.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
