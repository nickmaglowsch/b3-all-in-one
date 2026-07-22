"""Mixed methodology: value + quality + growth.

Each pillar is built from percentile ranks within the filtered universe.
Higher composite score = better. Weights are configurable.

Pillar construction:
    Value (40%): low ev/ebit, low p/l, reasonable p/vp
    Quality (35%): high roic, high roe, healthy ebit margin, low debt
    Growth (25%): positive 5y revenue growth (cresc. rec. 5a)

Note on percentile direction:
    For "lower is better" metrics (p/l, ev/ebit, p/vp, divbpatr) we invert.
    For "higher is better" metrics (roic, roe, mrgebit, c5y) we use directly.

The output 'score' is in [0, 1]. Higher = better on the chosen mix.
"""
from __future__ import annotations

import pandas as pd

from methodologies._util import debt_col, pct_rank as _pct_rank


DEFAULT_WEIGHTS = {"value": 0.40, "quality": 0.35, "growth": 0.25}


def build_pillars(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'value', 'quality', 'growth' columns (each in [0,1]) to a copy of df."""
    out = df.copy()

    # ---- VALUE: lower is better ----
    value_components = []
    if "evebit" in out.columns:
        # Negative EV/EBIT means negative EBIT or net cash > EV — treat as missing for ranking
        evebit = out["evebit"].where(out["evebit"] > 0)
        value_components.append(_pct_rank(evebit, ascending=True))
    if "pl" in out.columns:
        value_components.append(_pct_rank(out["pl"], ascending=True))
    if "pvp" in out.columns:
        pvp = out["pvp"].where(out["pvp"] > 0)
        value_components.append(_pct_rank(pvp, ascending=True))
    out["value"] = pd.concat(value_components, axis=1).mean(axis=1) if value_components else 0.0

    # ---- QUALITY: higher is better ----
    quality_components = []
    if "roic" in out.columns:
        quality_components.append(_pct_rank(out["roic"], ascending=False))
    if "roe" in out.columns:
        quality_components.append(_pct_rank(out["roe"], ascending=False))
    if "mrgebit" in out.columns:
        quality_components.append(_pct_rank(out["mrgebit"], ascending=False))
    # Lower debt = better quality. Use whichever debt/equity column is present
    # (Fundamentus renamed gross→net debt at some point; we accept either).
    dcol = debt_col(out)
    if dcol is not None:
        quality_components.append(_pct_rank(out[dcol], ascending=True))
    out["quality"] = pd.concat(quality_components, axis=1).mean(axis=1) if quality_components else 0.0

    # ---- GROWTH: higher is better ----
    growth_components = []
    if "c5y" in out.columns:
        # Cresc. Receita 5 anos — already a percentage
        growth_components.append(_pct_rank(out["c5y"], ascending=False))
    out["growth"] = pd.concat(growth_components, axis=1).mean(axis=1) if growth_components else 0.0

    return out


def score(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Add a 'score' column = weighted sum of pillars."""
    w = weights or DEFAULT_WEIGHTS
    total = sum(w.values())
    if abs(total - 1.0) > 1e-6:
        # Auto-normalize so user can pass {value: 6, quality: 4} etc.
        w = {k: v / total for k, v in w.items()}
    out = build_pillars(df)
    out["score"] = (
        out["value"] * w.get("value", 0)
        + out["quality"] * w.get("quality", 0)
        + out["growth"] * w.get("growth", 0)
    )
    return out.sort_values("score", ascending=False).reset_index(drop=True)
