"""What the benchmark says about the weather.

Split out of shared_data because it is a self-contained derivation — one price
series in, a handful of flags out — and because the percentile rank below has
more to say about itself than the rest of the bundle put together.

Every flag here is computed off `MarketSpec.benchmark`, so on US it already means
"the S&P" and on crypto "BTC". Only the names in the bundle still say IBOV; see
the aliases in shared_data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# The shared_data keys a regime input may name as its `flag`: one value per
# rebalance date, read by config_strategy._eval_regime as `flag.get(date)`. All
# are Series on the grid. A per-ticker frame (above_ma200, dist_ma200, any f_*)
# has no single value per date — `DataFrame.get(date)` looks up a *column* and
# returns None, so the input reads False forever and the book parks in CDI — and
# a daily series only hits on grid labels that are sessions (see
# DAILY_REGIME_FLAGS). scripts/validate_spec.py refuses anything else at save, and
# the bench's regime picker (frontend/lib/regime.ts) offers a subset of these.
REGIME_FLAGS = (
    "is_easing",
    "ibov_above", "ibov_calm", "ibov_uptrend", "ibov_vol_monthly", "ibov_vol_pctrank",
    "bench_above", "bench_calm", "bench_uptrend", "bench_vol_monthly", "bench_vol_pctrank",
    # Monthly returns, for a `gt` threshold ("CDI above 1% a month").
    "cdi_monthly", "ibov_ret", "bench_ret",
    # How far the benchmark is below its all-time high (full history), 0..1.
    "bench_drawdown",
)

# Daily benchmark series. They sit on the benchmark's own print dates, so they
# read correctly only on a market whose benchmark prints every calendar day
# (MarketSpec.benchmark_every_day: crypto). On B3 a month-end weekend reads
# False. cdi_cumul_63d is on BCB business days on every market, so never.
DAILY_REGIME_FLAGS = ("ibov_ret_20d_daily", "ibov_vol_20d_daily")


def regime_flags(spec) -> tuple[str, ...]:
    """The flags a regime input may read on market `spec` (a MarketSpec)."""
    return REGIME_FLAGS + (DAILY_REGIME_FLAGS if spec.benchmark_every_day else ())

# One-per-date Series an expression (a date x name frame) cannot read: pandas
# aligns a Series' dates against the frame's *tickers*, so the result is all NaN
# and the book silently sits in cash. bench_ret / ibov_ret / bench_px are
# documented expression data names that predate this list and stay readable.
PER_DATE = frozenset(REGIME_FLAGS + DAILY_REGIME_FLAGS) - {"bench_ret", "ibov_ret"} | {
    "cdi_daily", "cdi_cumul_63d"}
# Regime flags that are numbers, not booleans: an input reads them through `gt`
# (read bare, a drawdown or a return is "true" on almost every date).
NUMERIC_FLAGS = frozenset({"bench_drawdown", "bench_vol_pctrank", "ibov_vol_pctrank",
                           "bench_vol_monthly", "ibov_vol_monthly", "cdi_monthly",
                           "ibov_ret", "bench_ret", *DAILY_REGIME_FLAGS})


def _expanding_pctrank(s: pd.Series) -> pd.Series:
    """Where each observation sits in the distribution of everything before it.

    Replaces ``expanding(min_periods=12).apply(lambda x: (x.iloc[-1] >= x).mean())``,
    which rebuilt a Series per window and ran a Python callback per period — 11 ms
    over 20 years of month ends, 51 ms at weekly cadence, on every backtest.

    Two details of the callback are load-bearing and reproduced rather than
    tidied, because ibov_calm thresholds this at <= 0.70 and would flip:

      · it divides by the window *length*, not the non-NaN count, so leading gaps
        drag the rank down. Hence `/ arange`, not `rank(pct=True)`.
      · on a period with no observation, `NaN >= x` is all-False and the mean is
        0.0, not NaN — a missing month would read as "calmest ever". Preserved
        here; changing it is a signal change, not a refactor.

        That second branch turns out to be unreachable with real data: checked
        against 21 years of ^BVSP (2005-2026), `ibov_vol_20d` resampled to ME, QE
        and W-FRI has no post-warmup gaps at any of the three, because B3 never
        goes a whole month, quarter or Mon-Fri week without trading. It is kept
        as defensive behaviour, not because anything depends on it.
    """
    ranked = s.expanding(min_periods=12).rank(method="max")
    out = ranked / np.arange(1, len(s) + 1)
    return out.mask(s.isna() & s.notna().cumsum().ge(12), 0.0)


def benchmark_signals(bench_px: pd.Series, freq: str) -> dict:
    """The regime flags a strategy can gate on, on the rebalance grid.

    Keys: ``daily_ret``, ``vol_m`` (20-day vol at the grid), ``vol_pctrank``,
    ``calm`` (vol in the calmest 70% of history), ``uptrend`` (20-day return
    positive) and ``above`` (benchmark over its 10-period mean).
    """
    daily_ret = bench_px.pct_change()
    vol_m = daily_ret.rolling(20).std().resample(freq).last()
    vol_pctrank = _expanding_pctrank(vol_m)
    bench_m = bench_px.resample(freq).last()
    return {
        "daily_ret": daily_ret,
        "vol_m": vol_m,
        "vol_pctrank": vol_pctrank,
        "calm": vol_pctrank <= 0.70,
        "uptrend": bench_px.pct_change(20).resample(freq).last() > 0,
        "above": bench_m > bench_m.rolling(10).mean(),
    }


def drawdown_from_peak(bench_px: pd.Series, freq: str) -> pd.Series:
    """How far the benchmark sits below its all-time high, on the rebalance grid:
    ``1 - price / running max`` at each period's last session, in [0, 1].

    0 on a new high, 0.5 at half the peak. The running max is causal — the peak
    on a date is the highest close up to that date — and it is only an all-time
    high if `bench_px` starts at the start of history: shared_data feeds it the
    benchmark's full series, not the backtest's load window, so two runs of one
    spec over different windows read the same drawdown on the dates they share.

    Closes, not intraday highs: the plane carries `adj_close` for every
    benchmark and that is the price the rest of the bundle reads.
    """
    px = bench_px.dropna()
    px = px[px > 0]
    if px.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    dd = (1.0 - px / px.cummax()).clip(lower=0.0)
    return dd.resample(freq).last()
