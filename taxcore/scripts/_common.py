"""Today's price per ticker, for tlh_plan.

Kept dependency-free of the scripts that use it (no imports from sibling
scripts) so it can be imported anywhere without cycles.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
FII_SNAPSHOTS = DATA / "fii_snapshots"
EXTERNAL_PRICES = DATA / "external_prices"


def latest_csv(d: Path) -> pd.DataFrame | None:
    """Most recent CSV in directory `d` (snapshots are named by ISO date, so
    lexicographic sort is chronological), or None if there are none."""
    files = sorted(d.glob("*.csv")) if d.exists() else []
    return pd.read_csv(files[-1]) if files else None


def price_lookup() -> pd.DataFrame:
    """Union ticker→price across equity, FII, and external snapshots.

    Equity screener wins on collisions, then FII, then external. Returns a
    DataFrame with columns [ticker, price] (empty if no source is available).
    """
    sources: list[pd.DataFrame] = []
    for d in (SNAPSHOTS, FII_SNAPSHOTS, EXTERNAL_PRICES):
        snap = latest_csv(d)
        if snap is not None and {"papel", "cotacao"}.issubset(snap.columns):
            sources.append(snap[["papel", "cotacao"]])
    if not sources:
        return pd.DataFrame(columns=["ticker", "price"])
    return (
        pd.concat(sources, ignore_index=True)
        .rename(columns={"papel": "ticker", "cotacao": "price"})
        .drop_duplicates(subset=["ticker"], keep="first")
    )
