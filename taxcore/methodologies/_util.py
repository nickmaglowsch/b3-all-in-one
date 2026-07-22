"""Shared helpers for the ranking methodologies."""
from __future__ import annotations

import pandas as pd


def pct_rank(s: pd.Series, *, ascending: bool = True) -> pd.Series:
    """Percentile rank in [0, 1]. ascending=True means LOWER raw value → HIGHER score."""
    # rank() with pct=True gives [0, 1]. method='average' handles ties fairly.
    return s.rank(pct=True, ascending=not ascending, method="average")


def debt_col(df: pd.DataFrame) -> str | None:
    """Pick whichever debt/equity column the snapshot exposes.

    Fundamentus renamed gross→net debt ('divbpatr' → 'divlpatr') in 2025; we
    accept whichever is present so old and new snapshots both rank/filter.
    """
    if "divlpatr" in df.columns:
        return "divlpatr"
    if "divbpatr" in df.columns:
        return "divbpatr"
    return None
