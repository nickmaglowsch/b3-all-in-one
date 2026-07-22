"""Ibovespa Smart Low Volatility B3 — replica of B3's methodology.

B3's index (Retorno Total) selects, from the Ibovespa universe, the 33% of
companies with the LOWEST 252-day EWMA-smoothed annualized volatility, keeps
one asset per company (highest free-float market value), and weights each by
the INVERSE of that smoothed volatility, capped at 10%.

We reproduce the mechanics; two documented approximations (the framework has
no Ibov-membership feed nor free-float data):
  - Universe = the most liquid names (liq2m proxy for Ibovespa negociability;
    the snapshot retains delisted rows, so dead names with stale liquidity are
    included — only names now at liq2m=0 fall out of a top-N cut).
  - One-asset-per-company dedup keeps the higher-liquidity ticker of a shared
    root (PETR3/PETR4 -> the more traded), a proxy for max free-float MV.

EWMA note: the B3 annex writes sigma^2_n = a*sigma^2_{n-1} + (1-a)*R^2_{n-1}
with a = 2/(N+1). Read literally that puts ~99% weight on the latest squared
return, which contradicts a "252-day smoothed" measure — the a/(1-a) are
transposed vs the standard EMA convention (a = weight on the NEW observation,
giving an effective 252-day span). We follow the standard convention, i.e.
pandas ewm(alpha=2/(N+1)) on squared daily returns. Same as ewm(span=252).
"""
from __future__ import annotations

import pandas as pd


def ewma_vol(prices: pd.DataFrame, n: int = 252) -> pd.DataFrame:
    """Annualized EWMA volatility per column, as a daily time series.

    prices: wide frame [date x ticker] of closes. Returns the same shape with
    each cell = sqrt(EWMA(R^2) * 252) using alpha = 2/(n+1).
    """
    rets = prices.pct_change()
    var = (rets ** 2).ewm(alpha=2.0 / (n + 1), min_periods=n // 2).mean()
    return (var * 252.0) ** 0.5


def dedup_by_company(vol: pd.Series, liquidity: pd.Series) -> pd.Series:
    """Keep one ticker per company root: the highest-liquidity variant.

    Root = ticker minus its trailing class digit(s) (PETR3/PETR4 -> PETR,
    SANB11 -> SANB). Proxy for B3's 'asset with max free-float market value'.
    """
    root = vol.index.to_series().str.replace(r"\d+$", "", regex=True)
    keep = liquidity.reindex(vol.index).groupby(root.values).idxmax()
    return vol.loc[sorted(keep.values)]


def select_portfolio(vol: pd.Series, liquidity: pd.Series,
                     pct: float = 0.33, cap: float = 0.10) -> pd.Series:
    """Target weights for one rebalance from a cross-section of vols.

    vol: annualized EWMA vol per ticker (one date). liquidity: tie-breaker for
    the per-company dedup. Steps: dedup by company -> keep lowest-vol `pct` ->
    inverse-vol weight -> cap at `cap` (iterative, redistributing the excess to
    uncapped names) -> renormalize to 1.
    """
    vol = vol.dropna()
    vol = vol[vol > 0]
    if vol.empty:
        return pd.Series(dtype=float)
    vol = dedup_by_company(vol, liquidity)

    k = max(1, round(len(vol) * pct))
    chosen = vol.nsmallest(k)

    w = 1.0 / chosen
    w /= w.sum()
    # Iteratively cap at `cap`, spreading the overflow onto uncapped names.
    for _ in range(100):
        over = w[w > cap + 1e-12]
        if over.empty:
            break
        excess = (w[w > cap] - cap).sum()
        w[w > cap] = cap
        free = w[w < cap]
        if free.empty:
            break
        w[free.index] += excess * free / free.sum()
    return w / w.sum()
