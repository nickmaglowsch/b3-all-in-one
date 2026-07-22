"""Auto-populate data/watchlist.txt from top-ranked tickers not already held.

Loads the latest snapshot, applies hard filters, scores via the chosen
methodology, takes the top N tickers, drops anything already present in
data/positions.csv (unless --include-held), and writes the result to
data/watchlist.txt.

Usage:
    python -m scripts.update_watchlist
    python -m scripts.update_watchlist --top 50
    python -m scripts.update_watchlist --include-held
    python -m scripts.update_watchlist --output data/custom.txt

If the new file content matches the existing file byte-for-byte, the
script leaves the file alone (preserving mtime).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

import pandas as pd

from methodologies import filters, mixed

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "snapshots"
POSITIONS = ROOT / "data" / "positions.csv"
DEFAULT_OUTPUT = ROOT / "data" / "watchlist.txt"


def latest_snapshot() -> Path:
    files = sorted(SNAPSHOTS.glob("*.csv"))
    if not files:
        raise SystemExit("No snapshots in data/snapshots/. Run fetch_fundamentus.py first.")
    return files[-1]


def load_held_tickers() -> set[str]:
    if not POSITIONS.exists():
        return set()
    df = pd.read_csv(POSITIONS)
    if "ticker" not in df.columns:
        return set()
    return {str(t).strip().upper() for t in df["ticker"].dropna()}


def rank_top(df: pd.DataFrame, methodology: str, top_n: int) -> list[str]:
    if methodology != "mixed":
        raise SystemExit(f"Methodology '{methodology}' not implemented yet (only 'mixed').")
    ranked = mixed.score(df)
    return ranked.head(top_n)["papel"].tolist()


def build_content(tickers: list[str], methodology: str, top_n: int, include_held: bool) -> str:
    today = _dt.date.today().isoformat()
    suffix = f", top {top_n} " + ("including held" if include_held else "not-held")
    header = f"# auto-generated {today} from {methodology} methodology{suffix}\n"
    body = "\n".join(tickers)
    # Trailing newline so editors and shell tools are happy.
    return header + body + ("\n" if body else "")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top", type=int, default=30, help="Number of top-ranked tickers to consider (default: 30)")
    p.add_argument(
        "--include-held",
        action="store_true",
        help="Include tickers already in data/positions.csv (default: exclude them)",
    )
    p.add_argument(
        "--methodology",
        choices=["mixed"],
        default="mixed",
        help="Ranking methodology (default: mixed; 'value', 'quality', 'growth' reserved for future)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to write the watchlist (default: data/watchlist.txt)",
    )
    args = p.parse_args(argv[1:])

    snap = latest_snapshot()
    df = pd.read_csv(snap)
    print(f"Snapshot: {snap.name} ({len(df)} tickers)", file=sys.stderr)

    df = filters.apply(df)
    print(f"After hard filters: {len(df)} tickers", file=sys.stderr)

    top_tickers = rank_top(df, args.methodology, args.top)
    print(f"Top {args.top} after ranking: {len(top_tickers)} tickers", file=sys.stderr)

    held = load_held_tickers()
    print(f"Currently held: {len(held)} tickers", file=sys.stderr)

    if args.include_held:
        final = list(top_tickers)
    else:
        final = [t for t in top_tickers if t.upper() not in held]
        excluded = len(top_tickers) - len(final)
        print(f"Excluded {excluded} top-ranked names already held", file=sys.stderr)

    content = build_content(final, args.methodology, args.top, args.include_held)

    out_path: Path = args.output
    if out_path.exists() and out_path.read_text() == content:
        print(f"No change: {out_path} already matches generated content", file=sys.stderr)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    print(f"Wrote {len(final)} tickers to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
