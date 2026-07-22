"""Fetch detailed Fundamentus view for one ticker.

Usage: python -m scripts.fetch_ticker WEGE3
"""
from __future__ import annotations

import json
import sys

import fundamentus


def fetch(ticker: str) -> dict:
    df = fundamentus.get_papel(ticker.upper())
    if df is None or df.empty:
        raise SystemExit(f"No data returned for {ticker}")
    # get_papel returns a DataFrame with one row indexed by ticker
    row = df.iloc[0].to_dict()
    return row


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python -m scripts.fetch_ticker TICKER", file=sys.stderr)
        return 2
    ticker = argv[1]
    data = fetch(ticker)
    # Print as JSON for easy parsing by Claude or downstream tools
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
