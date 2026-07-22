"""Fetch prices for tickers Fundamentus doesn't cover (BDR-ETFs like IVVB11,
delisted names, etc.) using yfinance.

Reads data/positions.csv, finds tickers absent from today's equity + FII
snapshots, and pulls their last close from Yahoo Finance. Saves to
data/external_prices/YYYY-MM-DD.csv with the same shape as the snapshot
files (papel, cotacao) so portfolio_status.py can union them in.

yfinance uses '.SA' suffix for B3 tickers (e.g. IVVB11.SA). FIIs and
fractional markets are handled the same way.

Usage: python -m scripts.fetch_external_prices
"""
from __future__ import annotations

import sys
import warnings
from datetime import date
from pathlib import Path

import pandas as pd

from scripts._common import latest_csv as _latest_csv

# yfinance is noisy with deprecation/empty-data warnings on every ticker; this
# is a one-shot fetch CLI, so silence them process-wide rather than per call.
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "positions.csv"
SNAPSHOTS = ROOT / "data" / "snapshots"
FII_SNAPSHOTS = ROOT / "data" / "fii_snapshots"
EXTERNAL_DIR = ROOT / "data" / "external_prices"


def known_tickers() -> set[str]:
    """Tickers already priced by today's snapshots (equity + FII)."""
    out: set[str] = set()
    for snap in (_latest_csv(SNAPSHOTS), _latest_csv(FII_SNAPSHOTS)):
        if snap is not None and "papel" in snap.columns:
            out.update(snap["papel"].astype(str).str.upper())
    return out


def unpriced_tickers() -> list[str]:
    """Tickers in positions.csv that no snapshot has priced yet."""
    if not POSITIONS.exists():
        raise SystemExit("No positions.csv. Run load_b3.py first.")
    pos = pd.read_csv(POSITIONS)
    held = set(pos["ticker"].astype(str).str.upper())
    return sorted(held - known_tickers())


def fetch_prices(tickers: list[str]) -> pd.DataFrame:
    """Hit yfinance once per ticker, return a (papel, cotacao) DataFrame.

    Tickers that yfinance can't resolve (delisted, wrong suffix, etc.) are
    silently dropped — the caller surfaces them via the missing list.
    """
    import yfinance as yf
    rows: list[dict] = []
    for t in tickers:
        sym = f"{t}.SA"
        try:
            price = yf.Ticker(sym).fast_info.last_price
            if price and price > 0:
                rows.append({"papel": t, "cotacao": float(price), "source": "yfinance"})
        except Exception:
            # yfinance raises on delisted symbols; treat as unfetchable.
            continue
    return pd.DataFrame(rows, columns=["papel", "cotacao", "source"])


def main() -> int:
    todo = unpriced_tickers()
    if not todo:
        print("All held tickers are priced by Fundamentus snapshots — nothing to fetch.")
        return 0

    print(f"Fetching {len(todo)} tickers from yfinance: {todo}")
    df = fetch_prices(todo)
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = EXTERNAL_DIR / f"{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} prices → {out}")

    missing = sorted(set(todo) - set(df["papel"]))
    if missing:
        print(f"\n⚠️  yfinance had no data for: {missing}", file=sys.stderr)
        print("   Likely delisted or under a different ticker. "
              "Verify directly with your broker / B3 custody view.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
