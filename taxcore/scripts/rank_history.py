"""Walk all snapshots and show how a single ticker's score and rank evolved over time.

Useful for testing whether a flagged drop is recent or a long fade.

Usage:
    uv run python scripts/rank_history.py PETR4
    uv run python scripts/rank_history.py CAML3 --weights value=0.6,quality=0.3,growth=0.1
    uv run python scripts/rank_history.py UNIP6 --since 2025-01-01
    uv run python scripts/rank_history.py CAML3 --allow-units
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from methodologies import filters, mixed
from scripts._common import parse_weights

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "snapshots"


# Columns surfaced per row. Kept as a single source of truth so the row dict and
# the tabulate column ordering stay in sync.
NUMERIC_FIELDS = [
    "score", "value", "quality", "growth",
    "cotacao", "pl", "pvp", "evebit", "roic", "roe", "dy", "c5y",
]
DISPLAY_COLS = ["date", "rank", "n_filtered", *NUMERIC_FIELDS, "status"]


def list_snapshots(since: date | None) -> list[Path]:
    files = sorted(SNAPSHOTS.glob("*.csv"))
    if since is None:
        return files
    out: list[Path] = []
    for f in files:
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            # Skip oddly-named files rather than crash
            continue
        if d >= since:
            out.append(f)
    return out


def empty_row(snap_date: str, status: str) -> dict:
    """Row used when the ticker is absent or filtered out — numeric fields blank."""
    row: dict = {"date": snap_date, "rank": None, "n_filtered": None, "status": status}
    for f in NUMERIC_FIELDS:
        row[f] = None
    return row


def evaluate_snapshot(
    snap: Path,
    ticker: str,
    *,
    weights: dict[str, float] | None,
    allow_units: bool,
) -> dict:
    """Process one snapshot and return a row dict for the ticker."""
    snap_date = snap.stem  # filename is YYYY-MM-DD.csv
    df = pd.read_csv(snap)

    # 1. Is the ticker even in the raw snapshot?
    if ticker not in set(df["papel"]):
        return empty_row(snap_date, "not_in_snapshot")

    # 2. Apply hard filters
    filtered = filters.apply(df, allow_units=allow_units)
    n_filtered = len(filtered)

    if ticker not in set(filtered["papel"]):
        row = empty_row(snap_date, "filtered_out")
        row["n_filtered"] = n_filtered
        return row

    # 3. Score and rank
    ranked = mixed.score(filtered, weights=weights)
    # ranked is sorted descending by score and reset_index'd, so positional
    # index + 1 is the rank.
    match = ranked.index[ranked["papel"] == ticker]
    if len(match) == 0:
        # Shouldn't happen given the membership check above, but guard anyway.
        row = empty_row(snap_date, "filtered_out")
        row["n_filtered"] = n_filtered
        return row
    pos = int(match[0])
    rec = ranked.iloc[pos]

    row: dict = {
        "date": snap_date,
        "rank": pos + 1,
        "n_filtered": n_filtered,
        "status": "ok",
    }
    for f in NUMERIC_FIELDS:
        row[f] = rec[f] if f in ranked.columns else None
    return row


def round_for_display(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rr = dict(r)
        for f in NUMERIC_FIELDS:
            v = rr.get(f)
            if v is None or pd.isna(v):
                rr[f] = None
            else:
                rr[f] = round(float(v), 3)
        out.append(rr)
    return out


def summarize(rows: list[dict], ticker: str) -> str:
    """Produce the prose summary that goes below the table."""
    lines: list[str] = []

    ok_rows = [r for r in rows if r["status"] == "ok"]
    absent_rows = [r for r in rows if r["status"] == "not_in_snapshot"]
    filtered_rows = [r for r in rows if r["status"] == "filtered_out"]

    if not ok_rows:
        lines.append(f"{ticker} never appears in a passing/ranked state across the loaded snapshots.")
        if absent_rows:
            lines.append(f"  Absent from snapshot ({len(absent_rows)}): "
                         + ", ".join(r["date"] for r in absent_rows))
        if filtered_rows:
            lines.append(f"  Filtered out ({len(filtered_rows)}): "
                         + ", ".join(r["date"] for r in filtered_rows))
        return "\n".join(lines)

    scores = [r["score"] for r in ok_rows]
    ranks = [r["rank"] for r in ok_rows]

    lines.append(f"Score range: min={min(scores):.3f}, max={max(scores):.3f} "
                 f"(across {len(ok_rows)} ranked snapshot(s))")
    lines.append(f"Rank range: best={min(ranks)}, worst={max(ranks)}")

    # Trajectory — only meaningful with 2+ ranked points
    if len(ok_rows) == 1:
        lines.append("Trajectory: single data point, no history yet.")
    else:
        first, last = ok_rows[0]["rank"], ok_rows[-1]["rank"]
        delta = last - first  # rank: lower is better, so positive delta = decline
        # Stable threshold: ±5% of the filtered universe size, or ±5 absolute, whichever larger.
        # Use the latest n_filtered as a proxy for universe size.
        universe = ok_rows[-1].get("n_filtered") or 100
        stable_band = max(5, int(universe * 0.05))
        if abs(delta) <= stable_band:
            traj = "stable"
        elif delta < 0:
            traj = "improving"
        else:
            traj = "declining"
        lines.append(f"Rank trajectory: {traj} (first={first} → last={last}, delta={delta:+d}; "
                     f"stable band ±{stable_band})")

    if filtered_rows:
        lines.append(f"Filtered out on: " + ", ".join(r["date"] for r in filtered_rows))
    if absent_rows:
        lines.append(f"Absent from snapshot on: " + ", ".join(r["date"] for r in absent_rows))

    if len(rows) == 1:
        lines.append("Only 1 snapshot available; this is a point-in-time view rather than a "
                     "trajectory. Re-run after the next `fetch_fundamentus.py`.")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Show how a ticker's score and rank evolved across snapshots.")
    p.add_argument("ticker", type=str, help="e.g. PETR4")
    p.add_argument("--weights", type=str,
                   help="e.g. value=0.5,quality=0.3,growth=0.2 (default: methodology defaults)")
    p.add_argument("--since", type=str, help="YYYY-MM-DD; only include snapshots on/after this date")
    p.add_argument("--allow-units", action="store_true",
                   help="don't exclude tickers ending in 11 (passes through to filters.apply)")
    args = p.parse_args(argv[1:])

    ticker = args.ticker.strip().upper()
    weights = parse_weights(args.weights) if args.weights else None
    since = date.fromisoformat(args.since) if args.since else None

    snaps = list_snapshots(since)
    if not snaps:
        msg = "No snapshots in data/snapshots/"
        if since:
            msg += f" on/after {since.isoformat()}"
        msg += ". Run fetch_fundamentus.py first."
        raise SystemExit(msg)

    print(f"Walking {len(snaps)} snapshot(s) for {ticker}…", file=sys.stderr)

    rows = [
        evaluate_snapshot(snap, ticker, weights=weights, allow_units=args.allow_units)
        for snap in snaps
    ]
    # Already sorted ascending because list_snapshots returns sorted paths.

    display_rows = round_for_display(rows)
    table = [{c: r.get(c) for c in DISPLAY_COLS} for r in display_rows]

    print(tabulate(table, headers="keys", tablefmt="github"))
    print()
    print(summarize(rows, ticker))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
