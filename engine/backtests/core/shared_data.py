"""
Shared Data Builder
===================
Loads and precomputes all DataFrames needed by strategy plugins, once per run,
so every strategy in a backtest reads the same frames instead of each rebuilding
them. This is the single entry point the headless scripts/ runners go through.

**Once per run, and only if read.** The bundle is a ``memory.LazyShared``: the
monthly frames and the benchmark series are built up front (they are small, and
almost everything wants them), while every *daily* (date x ticker) panel is
registered as a thunk and built on first access. That distinction is the whole
point. A daily panel is `sessions x names`, and on the US plane over a 2000..today
window that is hundreds of MB apiece — the MA200, the ATR proxy and the five
mean-reversion features came to a multiple of the worker's entire memory limit,
built unconditionally, for strategies that read none of them. A US backtest died
with SIGKILL in the builder before a single signal had been generated.

Readers do not change: ``shared["ret"]``, ``shared.get("has_glitch")``,
``"atr_m" in shared`` and ``dict(shared)`` all behave as they did. See
backtests.core.memory for the mapping's contract.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from backtests.core import (
    fundamentals, markets, mean_rev_helpers, memory, splits, universe,
)
from backtests.core.data import (
    BASE_PANELS, load_market_panels, download_benchmark, download_cdi_daily,
    load_sector_pit,
)

logger = logging.getLogger(__name__)


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


def build_shared_data(
    db_path: str,
    start: str,
    end: str,
    freq: str = "ME",
    include_fundamentals: bool = False,
    market: str = markets.DEFAULT,
) -> dict:
    """
    Load and precompute all shared DataFrames needed by strategy plugins.

    Args:
        db_path:  Path to the market-plane database (file or DSN).
        start:    Start date string, e.g. '2005-01-01'.
        end:      End date string, e.g. '2025-12-31' or 'today'.
        freq:     Resampling frequency (default 'ME' = month-end).
        market:   Which market to trade — see backtests.core.markets. Defaults to
                  B3, so every shipped spec and every stored user strategy that
                  does not mention a market keeps its exact behaviour.

    Returns:
        A dict-like bundle of the precomputed DataFrames and Series — see the
        module docstring: the daily panels in it are built on first read. Prices
        are in BRL for every market: the numeraire is the holder's currency, not
        the venue's.
    """
    spec = markets.get(market)
    lookback = 12  # default lookback periods

    # ── 1. Load raw data ──────────────────────────────────────────────────────
    # The base panels only. The daily high and low are read separately and only
    # if something asks for them (see the deferred group below) — their one
    # consumer is the mean-reversion high-low range, and on the US plane they are
    # ~1.2 GB that most runs never look at.
    panels = load_market_panels(db_path, start, end, spec.key, panels=BASE_PANELS)
    adj_close = panels["adj_close"]
    split_adj_close = panels["split_adj_close"]
    close_px = panels["close"]
    fin_vol = panels["fin_volume"]
    del panels
    # CDI whatever the equity sleeve is: the cash a Brazilian holder parks in does
    # not change because the book is in US names.
    cdi_daily = download_cdi_daily(start, end)
    ibov_px = download_benchmark(spec.benchmark, start, end, market=spec.key)

    # ── 1b. Fix unrecorded splits ────────────────────────────────────────────
    # B3 only. The detector fires on a persistent, calm-volume overnight
    # repricing by a clean ratio — an unrecorded grupamento there, an ordinary
    # Tuesday in crypto, where it would silently rewrite price history.
    if spec.detect_splits:
        adj_close = splits.detect_and_fix_unrecorded(adj_close, close_px, fin_vol)

    # ── 2. Resample ───────────────────────────────────────────────────────────
    ibov_ret = ibov_px.resample(freq).last().pct_change().dropna()
    cdi_ret = (1 + cdi_daily).resample(freq).prod() - 1
    cdi_monthly = cdi_ret.copy()

    px = adj_close.resample(freq).last()
    ret = px.pct_change()
    raw_close = close_px.resample(freq).last()
    adtv = fin_vol.resample(freq).mean()
    # The daily close and turnover have now given up everything the common path
    # wants — the split fix above and these two monthly aggregates. Four
    # strategies read them daily, all of them B3 books where a panel is a few
    # tens of MB; on the US plane the pair is ~1.2 GB held for the whole run so
    # that one run in nine can have it. Dropped here and re-read on demand.
    del close_px, fin_vol

    log_ret = np.log1p(ret)
    # B3's -0.45 catches 2:1 (-50%) and 3:1 (-67%) splits that slip past the
    # heuristic detector. The thresholds come off the market spec because a
    # -45% month is not an artifact in crypto — applied there, this would mask
    # essentially the whole universe, permanently.
    has_glitch = ((ret > spec.glitch_up)
                  | (ret < spec.glitch_down)).rolling(lookback).max()

    # ── 3. Regime signals ─────────────────────────────────────────────────────
    # COPOM easing: CDI was lower 1 month ago vs 4 months ago
    is_easing = cdi_monthly.shift(1) < cdi_monthly.shift(4)

    # IBOV regime signals
    ibov_daily_ret = ibov_px.pct_change()
    ibov_vol_20d = ibov_daily_ret.rolling(20).std()
    ibov_vol_m = ibov_vol_20d.resample(freq).last()
    ibov_vol_pctrank = _expanding_pctrank(ibov_vol_m)
    ibov_calm = ibov_vol_pctrank <= 0.70

    ibov_ret_20d = ibov_px.pct_change(20)
    ibov_ret_m = ibov_ret_20d.resample(freq).last()
    ibov_uptrend = ibov_ret_m > 0

    ibov_m = ibov_px.resample(freq).last()
    ibov_ma10 = ibov_m.rolling(10).mean()
    ibov_above = ibov_m > ibov_ma10

    # Multifactor composite (momentum + low-vol)
    mom_sig = log_ret.shift(1).rolling(lookback).sum()
    mom_sig[has_glitch == 1] = np.nan
    vol_sig_mf = -ret.shift(1).rolling(lookback).std()
    vol_sig_mf[has_glitch == 1] = np.nan
    mf_composite = (
        mom_sig.rank(axis=1, pct=True) * 0.5
        + vol_sig_mf.rank(axis=1, pct=True) * 0.5
    )

    # Additional signals for Res.MultiFactor
    # Note: names reflect monthly-resampled rolling windows, not daily windows.
    # vol_5m = 5-period (month) rolling std, vol_2m = 2-period (month) rolling std.
    vol_5m = ret.rolling(5).std()
    vol_2m = ret.rolling(2).std()

    # IBOV vol series (monthly, for AdaptiveLowVol regime)
    ibov_vol_monthly = ibov_vol_m.copy()

    # The security master, read once. `universe.survivorship` would give the same
    # two frames, but it builds both eagerly and the mask is a full daily bool
    # panel that no strategy reads — so the read is hoisted here and the mask is
    # deferred with the rest of the daily work below.
    intervals = universe.load_listing_intervals(db_path, spec.key)

    shared = memory.LazyShared({
        # ── price / return data ───────────────────────────────────────────────
        "adj_close": adj_close,
        "split_adj_close": split_adj_close,
        "px": px,
        "ret": ret,
        "log_ret": log_ret,
        "raw_close": raw_close,
        "adtv": adtv,
        "has_glitch": has_glitch,
        # ── benchmark ─────────────────────────────────────────────────────────
        "ibov_ret": ibov_ret,
        "ibov_px": ibov_px,
        # Market-neutral aliases to the same objects. `ibov_*` cannot be renamed
        # — signals.yaml, every inline `expr:`, every hand-written strategy and
        # every stored tenant spec read those names — but a US or crypto spec
        # calling its benchmark "ibov" would be a lie on the page. New specs use
        # these; the old names stay and now mean "this market's benchmark".
        "bench_ret": ibov_ret,
        "bench_px": ibov_px,
        "market": spec.key,
        "cdi_monthly": cdi_monthly,
        "cdi_daily": cdi_daily,
        # ── regime signals ────────────────────────────────────────────────────
        "is_easing": is_easing,
        "ibov_calm": ibov_calm,
        "ibov_uptrend": ibov_uptrend,
        "ibov_above": ibov_above,
        "ibov_vol_monthly": ibov_vol_monthly,
        "ibov_vol_pctrank": ibov_vol_pctrank,
        # The same market-neutral aliasing as bench_ret / bench_px above, for the
        # regime flags. They are computed off `spec.benchmark`, so on US they
        # already mean "the S&P" and on crypto "BTC" — only the name said IBOV.
        # A regime spec reads these by name (config_strategy._eval_regime looks
        # the flag up in this dict), so a US or crypto spec can now gate on
        # `bench_above` and say what it means.
        "bench_above": ibov_above,
        "bench_calm": ibov_calm,
        "bench_uptrend": ibov_uptrend,
        "bench_vol_monthly": ibov_vol_monthly,
        "bench_vol_pctrank": ibov_vol_pctrank,
        # ── composite signals ─────────────────────────────────────────────────
        "mf_composite": mf_composite,
        "vol_5m": vol_5m,
        "vol_60d": vol_5m,   # backward-compat alias
        "vol_2m": vol_2m,
        "vol_20d": vol_2m,   # backward-compat alias
        # ── mean-reversion features that are benchmark/CDI series ────────────
        # Series, not panels: one column, so they cost nothing to build and stay
        # eager alongside the rest of the regime signals they belong with.
        "cdi_cumul_63d": mean_rev_helpers.cdi_cumul(cdi_daily),
        "ibov_vol_20d_daily": ibov_daily_ret.rolling(20).std().shift(1),
        "ibov_ret_20d_daily": ibov_px.pct_change(20),
        # ── survivorship (see backtests.core.universe) ────────────────────────
        # dense=False: the only reader is `universe.fill_returns`, which
        # reindexes this onto the returns matrix, and a dense daily grid here is
        # ~400 MB of NaN on the US plane to carry a few thousand real numbers.
        "delist_ret": universe.delisting_returns(
            intervals, adj_close.index, adj_close.columns, dense=False),
        # What this window's data cannot support, as codes. Unconditional and
        # self-contained: every branch lives inside coverage_notes, which
        # degrades to an empty list rather than costing a backtest its run.
        "data_warnings": universe.coverage_notes(db_path, spec.key, start, end),
    })

    # ── 4. Daily panels, built on first read ──────────────────────────────────
    #
    # Everything below is (sessions x names) at daily resolution. On B3 that is a
    # few hundred MB in total and nobody noticed; on the US plane it is several
    # gigabytes, which is how a 2000..today US run reached the worker's mem_limit
    # and came back as `killed by SIGKILL` with an empty stderr.
    #
    # Each thunk holds its own intermediates only for as long as it runs:
    # `_ma200_m` resamples the daily rolling mean and lets the daily frame go, and
    # `atr_m` recomputes the ATR proxy rather than reading
    # `shared["atr_20d_daily"]`, so asking for the monthly frame does not pin the
    # daily one for the rest of the run. A spec that reads both pays for the proxy
    # twice in CPU and once in memory, which is the right way round.

    def _ma200_m():
        return adj_close.rolling(200, min_periods=200).mean().resample(freq).last()

    def _atr_daily():
        return adj_close.pct_change().abs().ewm(span=14, min_periods=14).mean()

    def _reread(**names) -> dict:
        """Panels re-read from the plane, under the names the bundle uses.

        `load_market_panels` names a panel after the plane's column; the bundle
        has always called two of them `close_px` and `fin_vol`, and renaming
        those would reach every strategy, every stored spec and every
        signals.yaml entry.
        """
        got = load_market_panels(db_path, start, end, spec.key,
                                 panels=tuple(names.values()))
        return {key: got[panel] for key, panel in names.items()}

    # Regime frames off the 200-day mean.
    shared.defer("ma200_m", _ma200_m)
    shared.defer("above_ma200", lambda: px > shared["ma200_m"])
    shared.defer("dist_ma200", lambda: px / shared["ma200_m"] - 1)

    # The ATR proxy, daily and resampled.
    shared.defer("atr_20d_daily", _atr_daily)
    shared.defer("atr_m", lambda: _atr_daily().resample(freq).last())

    # Mean-reversion features. Each is derived independently so a spec that wants
    # one does not build the other four; the shared `pct_change` they each
    # recompute is one transient frame against five kept ones.
    shared.defer("autocorr_20d",
                 lambda: mean_rev_helpers.autocorr(adj_close, 20, 10))
    shared.defer("autocorr_60d",
                 lambda: mean_rev_helpers.autocorr(adj_close, 60, 30))
    shared.defer("rolling_vol_20d_daily",
                 lambda: mean_rev_helpers.rolling_vol_daily(adj_close, 20, 10))
    shared.defer("rolling_vol_60d_daily",
                 lambda: mean_rev_helpers.rolling_vol_daily(adj_close, 60, 30))
    shared.defer("high_low_range_20d",
                 lambda: mean_rev_helpers.high_low_range(
                     shared["split_adj_high"], shared["split_adj_low"],
                     split_adj_close))

    # The two panels that come back off the plane rather than off `adj_close`,
    # each pair in one read: the four strategies that want the daily close all
    # want the daily turnover beside it, and the high-low range needs both sides
    # of the range, so splitting either pair would only buy a second query.
    shared.defer_group(
        ("close_px", "fin_vol"),
        lambda: _reread(close_px="close", fin_vol="fin_volume"))
    shared.defer_group(
        ("split_adj_high", "split_adj_low"),
        lambda: _reread(split_adj_high="split_adj_high",
                        split_adj_low="split_adj_low"))

    # Nothing downstream reads the mask — it is the loader that stops the price
    # fill at a delisting — but it is part of the bundle's published shape.
    shared.defer("tradable", lambda: universe.tradable_mask(
        intervals, adj_close.index, adj_close.columns))

    # ── Point-in-time CVM sector classification (survivorship-free, from FCA) ──
    # Long-format [ticker, ref_date, sector]. Sourced from the FCA zips alongside
    # the DB (data/cvm). Any strategy can filter its universe by sector via
    # backtests.core.data.sector_membership. Degrades to empty if unavailable.
    try:
        fca_dir = Path(db_path).resolve().parent / "data" / "cvm"
        shared["sector_pit"] = load_sector_pit(fca_dir, db_path)
    except Exception as e:
        logger.warning(f"Could not load PIT sector data: {e}")
        shared["sector_pit"] = pd.DataFrame(columns=["ticker", "ref_date", "sector"])

    # Fundamentals are loaded and attached by their own module — see
    # backtests.core.fundamentals for the PIT semantics and the ratio inputs.
    if include_fundamentals:
        fundamentals.attach(shared, db_path, start, end, freq, ret, spec.key)

    return shared
