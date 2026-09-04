"""
Shared Data Builder
===================
Loads and precomputes all DataFrames needed by strategy plugins, once per run,
so every strategy in a backtest reads the same frames instead of each rebuilding
them. This is the single entry point the headless scripts/ runners go through.

**What a run keeps is the rebalance grid.** The bundle is a
``memory.LazyShared`` holding frames of `rebalances x names` — small, and what
almost every strategy actually reads. Daily `sessions x names` panels are
hundreds of MB apiece on the US plane, so none of them is resident: each is
registered as a thunk that queries the plane, and the grid itself is reduced from
streamed batches (see `_rebalance_grid`) so the market's bars are never all in
memory at once. Building them eagerly is what killed a US backtest with SIGKILL
before a single signal had been generated.

Readers do not change: ``shared["ret"]``, ``shared.get("has_glitch")``,
``"atr_m" in shared`` and ``dict(shared)`` all behave as they did. See
backtests.core.memory for the mapping's contract.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from backtests.core import (
    fundamentals, markets, mean_rev_helpers, memory, regimes, splits, universe,
)
from backtests.core import panels as plane
from backtests.core.data import (
    download_benchmark, download_cdi_daily, load_sector_pit,
)

logger = logging.getLogger(__name__)


# The bundle's names for the plane's panels. `close_px`/`fin_vol` are what every
# strategy and every stored spec has always called them; the plane calls the same
# columns `close` and `fin_volume`.
_PANEL_OF = {
    "adj_close": "adj_close",
    "split_adj_close": "split_adj_close",
    "split_adj_high": "split_adj_high",
    "split_adj_low": "split_adj_low",
    "close_px": "close",
    "fin_vol": "fin_volume",
}

# What the rebalance grid is reduced from. Not `split_adj_close`: nothing in the
# grid comes off it, and on the US plane it is ~580 MB of bars to read for that.
_GRID_PANELS = ("adj_close", "close", "fin_volume")


def _rebalance_grid(db_path: str, start: str, end: str, freq: str, spec,
                    sessions, symbols) -> tuple:
    """``(px, raw_close, adtv)`` on the rebalance grid, a few hundred names at a time.

    This is the change that made a US backtest fit. The grid is three frames of
    `rebalances x names` — 301 x 11,000 for a 2000..today US window, ~26 MB each —
    reduced from daily bars that are 6,540 x 11,000, ~580 MB a panel. Reading the
    market to build them meant holding twenty times the answer.

    Every step below is per ticker: the split fix is a per-column loop, and
    `resample().last()`/`.mean()` reduce each column independently. So a batch's
    contribution is its own columns of the whole-market result, and the
    concatenation is the frame the old whole-market load produced rather than an
    approximation of it. Cross-sectional work (the ranks in `mf_composite`, the
    liquidity dedup) happens afterwards, on the grid, where it is cheap.
    """
    px, raw, adtv = [], [], []
    for batch in plane.stream_market_panels(
            db_path, start, end, spec.key, panels=_GRID_PANELS,
            sessions=sessions, symbols=symbols):
        adj_close = batch["adj_close"]
        # B3 only. The detector fires on a persistent, calm-volume overnight
        # repricing by a clean ratio — an unrecorded grupamento there, an
        # ordinary Tuesday in crypto, where it would silently rewrite history.
        if spec.detect_splits:
            adj_close = splits.detect_and_fix_unrecorded(
                adj_close, batch["close"], batch["fin_volume"])
        px.append(adj_close.resample(freq).last())
        raw.append(batch["close"].resample(freq).last())
        # Turnover is not forward-filled, so this is the mean of the month's
        # actual prints — a day the name did not trade is missing, not a repeat.
        adtv.append(batch["fin_volume"].resample(freq).mean())
        del batch, adj_close

    if not px:            # a plane with no bars in the window; CI runs on one
        empty = pd.DataFrame(index=pd.DatetimeIndex([], name="date"),
                             columns=pd.Index([], name="ticker"), dtype=float)
        return empty, empty.copy(), empty.copy()
    # Every batch was aligned to the same sessions before it was reduced, so all
    # three concatenations are exact column unions with nothing to fill.
    return (pd.concat(px, axis=1), pd.concat(raw, axis=1), pd.concat(adtv, axis=1))


def _defer_derived(shared, freq: str, intervals, sessions, symbols) -> None:
    """Register what the bundle derives from daily bars — none of it built yet.

    Every frame here is (sessions x names) at daily resolution, or reduced from
    one: a few hundred MB on B3, several gigabytes on the US plane — which is how
    a 2000..today run reached the worker's mem_limit.

    Each thunk holds its intermediates only while it runs: `_ma200_m` resamples
    the daily rolling mean and lets the daily frame go, and `atr_m` recomputes the
    ATR proxy rather than reading `atr_20d_daily`, so asking for the monthly frame
    does not pin the daily one. A spec that reads both pays for the proxy twice in
    CPU and once in memory, which is the right way round.
    """
    def _ma200_m():
        return (shared["adj_close"].rolling(200, min_periods=200)
                .mean().resample(freq).last())

    def _atr_daily():
        return shared["adj_close"].pct_change().abs().ewm(
            span=14, min_periods=14).mean()

    def _mr(fn, *args):
        return lambda: fn(shared["adj_close"], *args)

    shared.defer("ma200_m", _ma200_m)
    shared.defer("above_ma200", lambda: shared["px"] > shared["ma200_m"])
    shared.defer("dist_ma200", lambda: shared["px"] / shared["ma200_m"] - 1)
    shared.defer("atr_20d_daily", _atr_daily)
    shared.defer("atr_m", lambda: _atr_daily().resample(freq).last())

    # Mean-reversion features, each derived on its own so a spec that wants one
    # does not build the other four; the `pct_change` they each recompute is one
    # transient frame against five kept ones.
    shared.defer("autocorr_20d", _mr(mean_rev_helpers.autocorr, 20, 10))
    shared.defer("autocorr_60d", _mr(mean_rev_helpers.autocorr, 60, 30))
    shared.defer("rolling_vol_20d_daily",
                 _mr(mean_rev_helpers.rolling_vol_daily, 20, 10))
    shared.defer("rolling_vol_60d_daily",
                 _mr(mean_rev_helpers.rolling_vol_daily, 60, 30))
    shared.defer("high_low_range_20d",
                 lambda: mean_rev_helpers.high_low_range(
                     shared["split_adj_high"], shared["split_adj_low"],
                     shared["split_adj_close"]))

    # Every daily panel, none resident until something reads one — the bundle's
    # half of "a panel is a query". One panel per ask, ~580 MB on the US plane,
    # rather than four of them for every run.
    for key in _PANEL_OF:
        shared.defer(key, functools.partial(shared["panel"], key))

    # Nothing downstream reads the mask — it is the loader that stops the price
    # fill at a delisting — but it is part of the bundle's published shape.
    shared.defer("tradable",
                 lambda: universe.tradable_mask(intervals, sessions, symbols))


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

    # ── 1. The market's two axes ─────────────────────────────────────────────
    # Everything below is expressed in terms of these: the sessions every batch
    # is aligned to, and the names the batches are cut from.
    sessions = plane.market_sessions(db_path, start, end, spec.key)
    symbols = plane.market_symbols(db_path, start, end, spec.key)
    # Two scans of the price table for two small lists. What they leave behind is
    # freed heap the allocator holds unless asked — ~250 MB on the US plane.
    memory.release()

    # CDI whatever the equity sleeve is: the cash a Brazilian holder parks in does
    # not change because the book is in US names.
    cdi_daily = download_cdi_daily(start, end)
    ibov_px = download_benchmark(spec.benchmark, start, end, market=spec.key)

    # ── 2. The rebalance grid, reduced batch by batch ────────────────────────
    px, raw_close, adtv = _rebalance_grid(
        db_path, start, end, freq, spec, sessions, symbols)
    ret = px.pct_change()

    ibov_ret = ibov_px.resample(freq).last().pct_change().dropna()
    cdi_ret = (1 + cdi_daily).resample(freq).prod() - 1
    cdi_monthly = cdi_ret.copy()

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
    bench = regimes.benchmark_signals(ibov_px, freq)
    ibov_daily_ret = bench["daily_ret"]
    ibov_vol_m = bench["vol_m"]
    ibov_vol_pctrank = bench["vol_pctrank"]
    ibov_calm = bench["calm"]
    ibov_uptrend = bench["uptrend"]
    ibov_above = bench["above"]

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

    # The security master, read once. `universe.survivorship` builds both frames
    # eagerly and the mask is a full daily bool panel no strategy reads, so the
    # read is hoisted here and the mask deferred with the rest of the daily work.
    intervals = universe.load_listing_intervals(db_path, spec.key)

    def _assemble(name: str, columns, reduce=None) -> pd.DataFrame:
        """A daily frame, streamed and written straight into its own columns.

        The bundle's escape hatch, and the reason the daily panels can be absent
        from it: a strategy that genuinely reduces daily bars itself asks for one
        and gets it, and a caller that needs only some names says which.

        Never gathered. Asking `load_market_panels` for ten thousand symbols in
        one query is the whole-market read this design exists to avoid — measured
        at US scale it cost 5.6 GB to produce a 508 MB frame. Filling a
        preallocated block a batch at a time makes the peak the frame plus a batch.

        `reduce` is applied to each batch before it lands. It must be per-ticker —
        the property the whole module rests on — and then reducing the batches is
        the same as reducing the frame, without ever building the frame.
        """
        wanted = _PANEL_OF[name]
        # An unrecorded grupamento is corrected against the raw close and the
        # turnover of the same bars, so a corrected adj_close cannot be read
        # alone. B3 only, and the detector is a per-column loop, so a batch
        # corrects its own names.
        extra = ("close", "fin_volume") if (
            wanted == "adj_close" and spec.detect_splits) else ()
        cols = list(symbols if columns is None else columns)
        index = pd.DatetimeIndex(sessions, name="date")
        out = np.full((len(index), len(cols)), np.nan)
        at = {c: i for i, c in enumerate(cols)}
        for batch in plane.stream_market_panels(
                db_path, start, end, spec.key, panels=(wanted, *extra),
                sessions=sessions, symbols=cols):
            frame = batch[wanted]
            if extra:
                frame = splits.detect_and_fix_unrecorded(
                    frame, batch["close"], batch["fin_volume"])
            if reduce is not None:
                frame = reduce(frame)
            # By name, not by position: a symbol the caller asked for that the
            # plane has no bars for is absent from the batch, and every column
            # after it would otherwise land one place to the left.
            out[:, [at[c] for c in frame.columns]] = frame.to_numpy()
            del batch, frame
        return pd.DataFrame(out, index=index, columns=pd.Index(cols, name="ticker"))

    def read_panel(name: str, columns=None) -> pd.DataFrame:
        """One daily panel, for `columns` or for the whole market."""
        return _assemble(name, columns)

    def read_daily_returns(name: str = "split_adj_close",
                           columns=None) -> pd.DataFrame:
        """Daily returns of one panel, without ever building the panel.

        `pct_change` is per ticker, so it belongs in the batch. Reading the prices
        and then diffing them holds both at once — half a gigabyte of US prices
        kept alive only to be subtracted from itself.
        """
        return _assemble(name, columns, reduce=lambda f: f.pct_change())

    shared = memory.LazyShared({
        # ── price / return data ───────────────────────────────────────────────
        # `panel` is not a frame: it is how a caller asks for daily bars, by name
        # and optionally by ticker. See read_panel — and note that the daily
        # panels themselves are deferred below, so reading one is a query.
        "panel": read_panel,
        "daily_returns": read_daily_returns,
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
        # Series, not panels: one column, so they stay eager alongside the regime
        # signals they belong with.
        "cdi_cumul_63d": mean_rev_helpers.cdi_cumul(cdi_daily),
        "ibov_vol_20d_daily": ibov_daily_ret.rolling(20).std().shift(1),
        "ibov_ret_20d_daily": ibov_px.pct_change(20),
        # ── survivorship (see backtests.core.universe) ────────────────────────
        # dense=False: its only reader reindexes it, and a dense daily grid is
        # ~400 MB of NaN to carry a few thousand real numbers.
        "delist_ret": universe.delisting_returns(
            intervals, sessions, symbols, dense=False),
        # What this window's data cannot support, as codes. Every branch lives
        # inside coverage_notes, which degrades to [] rather than failing a run.
        "data_warnings": universe.coverage_notes(db_path, spec.key, start, end),
    })

    _defer_derived(shared, freq, intervals, sessions, symbols)

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

    # The boundary between loading the market and running a strategy over it.
    # Building the grid-level frames — the ranks in mf_composite, the rolling
    # windows, the glitch mask — churns through a dozen temporaries the size of
    # the grid, and on the US plane that is ~500 MB the allocator keeps unless
    # asked. Nothing above is still needed; hand it back before the strategy
    # starts, so what it sees is a floor and not somebody else's high-water mark.
    memory.release()
    return shared
