"""Shared helpers across the analysis / portfolio scripts.

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
    """Most recent CSV in directory `d`, or None if there are none."""
    if not d.exists():
        return None
    files = sorted(d.glob("*.csv"))
    return pd.read_csv(files[-1]) if files else None


def parse_weights(s: str) -> dict[str, float]:
    """Parse 'value=0.5,quality=0.3' → {'value': 0.5, 'quality': 0.3}."""
    out: dict[str, float] = {}
    for part in s.split(","):
        k, v = part.split("=")
        out[k.strip()] = float(v)
    return out


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


def annualize(pct: float, years: float) -> float:
    """CAGR (% per year) from a total return %. NaN if undefined (≤0 years or
    a loss worse than -100%)."""
    if years <= 0:
        return float("nan")
    growth = 1.0 + pct / 100.0
    if growth <= 0:
        return float("nan")
    return (growth ** (1.0 / years) - 1.0) * 100.0
