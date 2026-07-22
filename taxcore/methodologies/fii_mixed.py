"""Mixed FII methodology: yield + discount + quality.

FIIs aren't analyzed like equities. Earnings momentum and ROIC mostly
don't apply because most FIIs distribute >95% of net income. The pillars
that matter are:

    Yield (50%):     how much cash the fund actually pays out today
                     (DY) and how sustainable it is (FFO Yield close to
                     DY = distributions backed by real operating income).
    Discount (25%):  P/VP < 1 means trading below NAV. Cheap can mean
                     opportunity OR distressed — pair with quality.
    Quality (25%):   for tijolo (real estate) FIIs: low vacância +
                     enough properties to absorb a single-tenant hit.
                     For papel/FOFs: vacância is 0 by nature, so they
                     get full marks here — their risk lives elsewhere
                     (credit quality of the receivables) and isn't in
                     the screener data.

Higher composite score = better. Weights configurable.

Note: when a fund's FFO Yield diverges far from DY (e.g. DY 12% vs FFO
Yield 4%), the distribution is being paid from sources other than
operating income — sale gains, capital, or debt. We surface this via
the 'dy_ffo_gap' column for review but don't auto-penalize, since the
gap can be legitimate (e.g. FOFs receiving amortizations from sub-funds).
"""
from __future__ import annotations

import pandas as pd

from methodologies._util import pct_rank as _pct_rank


DEFAULT_WEIGHTS = {"yield": 0.50, "discount": 0.25, "quality": 0.25}


def build_pillars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # ---- YIELD: higher is better ----
    yield_components: list[pd.Series] = []
    if "dy" in out.columns:
        yield_components.append(_pct_rank(out["dy"], ascending=False))
    if "ffo_yield" in out.columns:
        # FFO Yield can be NaN or negative for funds without real operating
        # income (FOFs, brand-new funds). Treat negative/NaN as missing.
        ffo = out["ffo_yield"].where(out["ffo_yield"] > 0)
        yield_components.append(_pct_rank(ffo, ascending=False))
    out["yield"] = (pd.concat(yield_components, axis=1).mean(axis=1)
                    if yield_components else 0.0)

    # ---- DISCOUNT: lower P/VP is better ----
    if "pvp" in out.columns:
        out["discount"] = _pct_rank(out["pvp"], ascending=True)
    else:
        out["discount"] = 0.0

    # ---- QUALITY: lower vacância (when meaningful) + enough properties ----
    quality_components: list[pd.Series] = []
    if "vacancia" in out.columns:
        # Lower vacância = better. Papel FIIs / FOFs report 0, which gives
        # them top scores here — accurate within the data we have.
        # Paper/credit FIIs (qtd_imoveis == 0) hold receivables, not buildings,
        # so vacância is meaningless and Fundamentus sometimes reports it as
        # 1.0 (100%) for such funds — which would unfairly tank their quality.
        # Mask to NaN there so the percentile rank ignores them on this axis.
        if "qtd_imoveis" in out.columns:
            vacancia = out["vacancia"].where(out["qtd_imoveis"] > 0)
        else:
            vacancia = out["vacancia"]
        quality_components.append(_pct_rank(vacancia, ascending=True))
    if "qtd_imoveis" in out.columns:
        # More properties = more diversification. But many segments (papel,
        # FOFs) legitimately have 0 imóveis. Apply this only when > 0;
        # otherwise treat as missing.
        imoveis = out["qtd_imoveis"].where(out["qtd_imoveis"] > 0)
        quality_components.append(_pct_rank(imoveis, ascending=False))
    if quality_components:
        out["quality"] = pd.concat(quality_components, axis=1).mean(axis=1)
        # Pure paper/credit FIIs (qtd_imoveis == 0) have BOTH vacância and
        # qtd_imoveis masked, leaving them with no quality signal at all
        # (NaN). Treat that as neutral (0.5) instead of NaN — we can't
        # measure their quality from the screener, so we shouldn't punish
        # or reward them on this axis.
        out["quality"] = out["quality"].fillna(0.5)
    else:
        out["quality"] = 0.0

    # Diagnostic: gap between distributed yield and FFO yield. Big positive
    # gaps (DY >> FFO) are worth investigating — distributions outrunning
    # operating income.
    if {"dy", "ffo_yield"}.issubset(out.columns):
        out["dy_ffo_gap"] = (out["dy"] - out["ffo_yield"]).round(4)

    return out


def score(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    w = weights or DEFAULT_WEIGHTS
    total = sum(w.values())
    if abs(total - 1.0) > 1e-6:
        # Auto-normalize so callers can pass {yield: 6, quality: 4} etc.
        w = {k: v / total for k, v in w.items()}
    out = build_pillars(df)
    out["score"] = (
        out["yield"] * w.get("yield", 0)
        + out["discount"] * w.get("discount", 0)
        + out["quality"] * w.get("quality", 0)
    )
    return out.sort_values("score", ascending=False).reset_index(drop=True)
