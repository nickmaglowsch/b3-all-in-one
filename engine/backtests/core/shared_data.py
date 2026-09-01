"""
Shared Data Builder
===================
Loads and precomputes all DataFrames needed by strategy plugins, once per run,
so every strategy in a backtest reads the same frames instead of each rebuilding
them. This is the single entry point the headless scripts/ runners go through.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from backtests.core import universe
from backtests.core.data import (
    load_b3_hlc_data, download_benchmark, download_cdi_daily,
    load_all_fundamentals, load_all_fundamentals_monthly,
    compute_pe_ratio_dynamic, compute_pb_ratio_dynamic, compute_ev_ebitda_dynamic,
    load_sector_pit,
)
from backtests.core.mean_rev_helpers import compute_mean_rev_features

logger = logging.getLogger(__name__)

# Common split ratios (N:1 forward splits)
_SPLIT_RATIOS = [2, 3, 4, 5, 8, 10]
_SPLIT_TOLERANCE = 0.04  # 4% tolerance for ratio matching
_VOLUME_SPIKE = 3.0      # event-day BRL volume >= 3x trailing median => crash, not split


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


def _find_split_events(
    adj_s: pd.Series, raw_s: pd.Series, vol_s: pd.Series | None
) -> list[tuple]:
    """Dates where an unrecorded split is the only reading left, as
    [(date, N, "forward"|"reverse"), ...] in ascending date order.

    Every test below is applied to the whole series at once. It used to be a
    per-bar Python loop doing `adj_ratio.iloc[i]` — roughly 700 tickers x 5 000
    bars of pandas scalar indexing on every backtest and every advise call,
    which is the single hottest thing build_shared_data did. The decisions are
    unchanged and pinned bar-for-bar by tests/test_unrecorded_split_detector.py;
    only the loop is gone.

    `raw_s` and `adj_s` share one gap-free index (the caller drops NaNs and
    intersects), so "position i" here is a real trading bar, not a calendar day.
    """
    adj_ratio = (adj_s / adj_s.shift(1)).iloc[1:]
    raw_ratio = (raw_s / raw_s.shift(1)).iloc[1:]
    if adj_ratio.empty:
        return []

    ar = adj_ratio.to_numpy(dtype=float)
    rr = raw_ratio.to_numpy(dtype=float)

    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(ar) & np.isfinite(rr) & (rr != 0)
        # Both adj and raw must show nearly the same ratio — that is what says
        # no adjustment was applied. Where they disagree, the split is already in.
        ok &= np.abs(ar - rr) / np.maximum(np.abs(rr), 0.001) <= 0.05

    # Forward splits sit at ratio ~ 1/N, reverse at ~ N. The scan order matters
    # and is preserved: ascending N, forward before reverse at each N, first hit
    # wins — the old loop's `break`.
    n_hit = np.zeros(ar.shape, dtype=np.int64)
    is_forward = np.zeros(ar.shape, dtype=bool)
    unmatched = ok.copy()
    with np.errstate(invalid="ignore"):
        for n in _SPLIT_RATIOS:
            target = 1.0 / n
            hit = unmatched & (np.abs(ar - target) / target < _SPLIT_TOLERANCE)
            n_hit[hit], is_forward[hit], unmatched = n, True, unmatched & ~hit
            hit = unmatched & (np.abs(ar - float(n)) / n < _SPLIT_TOLERANCE)
            n_hit[hit], is_forward[hit], unmatched = n, False, unmatched & ~hit
    keep = ok & (n_hit > 0)
    if not keep.any():
        return []

    # Persistence: the following days must stay in the new regime, and the
    # preceding days must have been in the old regime. A one-day data error
    # fails one of the two (either it reverts the next day, or the "old level"
    # was itself the single glitch day) — rewriting all prior history off a
    # glitch would be catastrophic.
    #
    # Both windows are rolling medians in disguise. The event sits at position
    # `pos` in raw_s, one ahead of its position in the ratio series:
    #   forward window  raw_s[pos+1 : pos+6]  -> reversed rolling(5), shifted back 1
    #   backward window raw_s[pos-6 : pos-1]  -> rolling(5), shifted forward 2
    # min_periods=1 reproduces the short windows at either end, and the NaN the
    # shift leaves behind is exactly where the old slice came back empty and the
    # check was skipped.
    fwd_med = raw_s.iloc[::-1].rolling(5, min_periods=1).median().iloc[::-1].shift(-1)
    bwd_med = raw_s.rolling(5, min_periods=1).median().shift(2)

    at = adj_ratio.index
    cur = raw_s.reindex(at).to_numpy(dtype=float)            # raw_s.iloc[pos]
    prev = raw_s.shift(1).reindex(at).to_numpy(dtype=float)  # raw_s.iloc[pos - 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        lvl = fwd_med.reindex(at).to_numpy(dtype=float) / cur
        lvl_prv = bwd_med.reindex(at).to_numpy(dtype=float) / prev
        # NaN == the window was empty == check skipped. A zero denominator gives
        # inf, which falls outside the band and rejects, as it did before.
        keep &= np.isnan(lvl) | ((lvl >= 0.6) & (lvl <= 1.5))
        keep &= np.isnan(lvl_prv) | ((lvl_prv >= 0.6) & (lvl_prv <= 1.5))

    # Volume corroboration: a real crash/repricing prints anomalous financial
    # volume; a genuine split trades near normal BRL volume (price/N x shares*N
    # cancels). Computed on the full column, then looked up by date — a ticker
    # absent from the volume frame simply skips the check.
    if vol_s is not None:
        vol_med = vol_s.rolling(20, min_periods=5).median().shift(1)
        v = vol_s.reindex(at).to_numpy(dtype=float)
        m = vol_med.reindex(at).to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            spike = ~np.isnan(v) & ~np.isnan(m) & (m > 0) & (v >= _VOLUME_SPIKE * m)
        keep &= ~spike

    return [
        (at[i], int(n_hit[i]), "forward" if is_forward[i] else "reverse")
        for i in np.flatnonzero(keep)
    ]


def _detect_and_fix_unrecorded_splits(
    adj_close: pd.DataFrame,
    close_px: pd.DataFrame,
    fin_vol: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Detect stock splits that were not adjusted by the pipeline and correct adj_close.

    When B3's API doesn't return historical split data (common for events before ~2010),
    adj_close == close_px and the split shows as an overnight price drop matching a common
    split ratio (e.g., exactly -50% for a 2:1 split).

    Algorithm:
    1. For each stock, compute daily price ratio (today/yesterday) for both adj_close
       and close_px.
    2. If both series show the same ratio AND that ratio matches a common split pattern
       (1/2, 1/3, 1/4, etc. within tolerance), flag it as an unrecorded split.
    3. Correct adj_close by dividing all pre-split prices by the split ratio.

    This is conservative: only fires when both adjusted and raw prices show identical
    split-like patterns (confirming no adjustment was applied), the following days
    stay in the new price regime (one-day data glitches revert), and — when volume
    data is available — the event day traded at normal financial volume (a real
    crash prints anomalous volume; a split keeps BRL volume ~flat since
    price/N x shares*N cancels).

    # ponytail: a calm-volume, persistent overnight repricing landing within the
    # ratio tolerance of an exact 1/N still gets rewritten; the real fix is
    # cross-checking recorded corporate actions in the DB around the event date.
    """
    corrected = adj_close.copy()
    total_fixes = 0

    for col in corrected.columns:
        adj_s = corrected[col].dropna()
        raw_s = close_px[col].reindex(adj_s.index).dropna()

        vol_s = None
        if fin_vol is not None and col in fin_vol.columns:
            vol_s = fin_vol[col]

        common_idx = adj_s.index.intersection(raw_s.index)
        if len(common_idx) < 2:
            continue

        split_events = _find_split_events(
            adj_s.loc[common_idx], raw_s.loc[common_idx], vol_s
        )
        if not split_events:
            continue

        # Apply corrections: for a forward N:1 split at date D,
        # all prices BEFORE D need to be divided by N (backward adjustment)
        for dt, n, direction in split_events:
            mask = corrected.index < dt
            if direction == "forward":
                corrected.loc[mask, col] /= n
            else:  # reverse split
                corrected.loc[mask, col] *= n

        total_fixes += len(split_events)
        logger.info(
            f"Fixed {len(split_events)} unrecorded splits for {col}: "
            + ", ".join(f"{d.date()} {dir} {n}:1" for d, n, dir in split_events)
        )

    if total_fixes > 0:
        logger.info(f"Total unrecorded splits fixed: {total_fixes}")
    return corrected


def build_shared_data(
    db_path: str,
    start: str,
    end: str,
    freq: str = "ME",
    include_fundamentals: bool = False,
) -> dict:
    """
    Load and precompute all shared DataFrames needed by strategy plugins.

    Args:
        db_path:  Path to the B3 SQLite database file.
        start:    Start date string, e.g. '2005-01-01'.
        end:      End date string, e.g. '2025-12-31' or 'today'.
        freq:     Resampling frequency (default 'ME' = month-end).

    Returns:
        Dict containing all precomputed DataFrames and Series.
    """
    lookback = 12  # default lookback periods

    # ── 1. Load raw data ──────────────────────────────────────────────────────
    adj_close, split_adj_high, split_adj_low, split_adj_close, close_px, fin_vol = (
        load_b3_hlc_data(db_path, start, end)
    )
    _tradable, _delist_ret = universe.survivorship(db_path, adj_close)
    cdi_daily = download_cdi_daily(start, end)
    ibov_px = download_benchmark("^BVSP", start, end)

    # ── 1b. Fix unrecorded splits ────────────────────────────────────────────
    adj_close = _detect_and_fix_unrecorded_splits(adj_close, close_px, fin_vol)

    # ── 2. Resample ───────────────────────────────────────────────────────────
    ibov_ret = ibov_px.resample(freq).last().pct_change().dropna()
    cdi_ret = (1 + cdi_daily).resample(freq).prod() - 1
    cdi_monthly = cdi_ret.copy()

    px = adj_close.resample(freq).last()
    ret = px.pct_change()
    raw_close = close_px.resample(freq).last()
    adtv = fin_vol.resample(freq).mean()

    log_ret = np.log1p(ret)
    # Threshold -0.45 catches 2:1 splits (-50%) and 3:1 splits (-67%)
    # that slip past the heuristic detector
    has_glitch = ((ret > 1.0) | (ret < -0.45)).rolling(lookback).max()

    # ── 3. Regime signals ─────────────────────────────────────────────────────
    # COPOM easing: CDI was lower 1 month ago vs 4 months ago
    is_easing = cdi_monthly.shift(1) < cdi_monthly.shift(4)

    # MA200 and above-MA200 flag
    ma200_daily = adj_close.rolling(200, min_periods=200).mean()
    ma200_m = ma200_daily.resample(freq).last()
    above_ma200 = px > ma200_m
    dist_ma200 = px / ma200_m - 1

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
    daily_ret_abs = adj_close.pct_change().abs()
    atr_proxy = daily_ret_abs.ewm(span=14, min_periods=14).mean()
    atr_20d_daily = atr_proxy  # daily ATR series (pre-resample) for ratio-to-mean60 signals
    atr_m = atr_proxy.resample(freq).last()
    vol_2m = ret.rolling(2).std()

    # IBOV vol series (monthly, for AdaptiveLowVol regime)
    ibov_vol_monthly = ibov_vol_m.copy()

    # ── 4. Mean-reversion composite features (daily frequency) ────────────────
    mr_features = compute_mean_rev_features(
        adj_close, split_adj_high, split_adj_low, split_adj_close,
        cdi_daily, ibov_daily_ret, ibov_px,
    )
    autocorr_20d = mr_features["autocorr_20d"]
    autocorr_60d = mr_features["autocorr_60d"]
    high_low_range_20d = mr_features["high_low_range_20d"]
    rolling_vol_20d_daily = mr_features["rolling_vol_20d_daily"]
    rolling_vol_60d_daily = mr_features["rolling_vol_60d_daily"]
    cdi_cumul_63d = mr_features["cdi_cumul_63d"]
    ibov_vol_20d_daily = mr_features["ibov_vol_20d_daily"]
    ibov_ret_20d_daily = mr_features["ibov_ret_20d_daily"]

    shared = {
        # ── price / return data ───────────────────────────────────────────────
        "adj_close": adj_close,
        "split_adj_high": split_adj_high,
        "split_adj_low": split_adj_low,
        "split_adj_close": split_adj_close,
        "close_px": close_px,
        "fin_vol": fin_vol,
        "px": px,
        "ret": ret,
        "log_ret": log_ret,
        "raw_close": raw_close,
        "adtv": adtv,
        "has_glitch": has_glitch,
        # ── benchmark ─────────────────────────────────────────────────────────
        "ibov_ret": ibov_ret,
        "ibov_px": ibov_px,
        "cdi_monthly": cdi_monthly,
        "cdi_daily": cdi_daily,
        # ── regime signals ────────────────────────────────────────────────────
        "is_easing": is_easing,
        "above_ma200": above_ma200,
        "dist_ma200": dist_ma200,
        "ma200_m": ma200_m,
        "ibov_calm": ibov_calm,
        "ibov_uptrend": ibov_uptrend,
        "ibov_above": ibov_above,
        "ibov_vol_monthly": ibov_vol_monthly,
        "ibov_vol_pctrank": ibov_vol_pctrank,
        # ── composite signals ─────────────────────────────────────────────────
        "mf_composite": mf_composite,
        "vol_5m": vol_5m,
        "vol_60d": vol_5m,   # backward-compat alias
        "atr_m": atr_m,
        "atr_20d_daily": atr_20d_daily,
        "vol_2m": vol_2m,
        "vol_20d": vol_2m,   # backward-compat alias
        # ── mean-reversion composite features ────────────────────────────────
        "autocorr_20d": autocorr_20d,
        "autocorr_60d": autocorr_60d,
        "high_low_range_20d": high_low_range_20d,
        "rolling_vol_20d_daily": rolling_vol_20d_daily,
        "rolling_vol_60d_daily": rolling_vol_60d_daily,
        "cdi_cumul_63d": cdi_cumul_63d,
        "ibov_vol_20d_daily": ibov_vol_20d_daily,
        "ibov_ret_20d_daily": ibov_ret_20d_daily,
        # ── survivorship (see backtests.core.universe) ────────────────────────
        "tradable": _tradable,
        "delist_ret": _delist_ret,
    }

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

    # ── Optional: CVM fundamentals (PIT-aligned to rebalance calendar) ────────
    # When include_fundamentals=True, loads all 10 metric DataFrames from
    # fundamentals_pit and adds them with "f_" prefix. Default is False to
    # avoid performance regression for strategies that don't need fundamentals.
    if include_fundamentals:
        logger.info("Loading CVM fundamentals data (include_fundamentals=True)...")
        # Pass the ticker universe so the root-keyed fundamentals_pit frames are
        # broadcast onto full tickers (else they silently fail to join — the bug
        # that made ValueQuality never invest).
        fundamentals = load_all_fundamentals(db_path, start, end, freq=freq, tickers=ret.columns)
        for metric, df_fund in fundamentals.items():
            shared["f_" + metric] = df_fund
        logger.info(f"Added {len(fundamentals)} fundamentals DataFrames with 'f_' prefix")

        # ── Monthly snapshot — f_*_m (raw financials, all use '_m' suffix) ─────
        # Reads from fundamentals_monthly (pre-materialized by pipeline step 9b).
        # All raw financial metrics get '_m' suffix.
        # Ratio keys (f_pe_ratio_dyn, f_pb_ratio_dyn, f_ev_ebitda_dyn) are computed
        # dynamically below from raw inputs — not loaded from stored columns.
        # Gracefully degrades if the table is empty or doesn't exist yet.
        try:
            fundamentals_monthly = load_all_fundamentals_monthly(db_path, start, end)
            rebal_dates = ret.index  # use the strategy's rebalance calendar
            for metric, df_m in fundamentals_monthly.items():
                key = "f_" + metric + "_m"
                if df_m.empty:
                    shared[key] = pd.DataFrame(index=rebal_dates, dtype=float)
                else:
                    # Reindex monthly snapshot to the strategy's rebalance calendar
                    extended_idx = df_m.index.union(rebal_dates).sort_values()
                    df_aligned = df_m.reindex(extended_idx).ffill().reindex(rebal_dates)
                    df_aligned = df_aligned.loc[df_aligned.index >= pd.Timestamp(start)]
                    shared[key] = df_aligned
            logger.info(
                f"Added {len(fundamentals_monthly)} monthly fundamentals DataFrames "
                f"with '_m' suffix"
            )
        except Exception as e:
            logger.warning(
                f"Failed to load fundamentals_monthly (table may not exist or be empty): {e}. "
                f"Adding empty DataFrames for all _m keys."
            )
            rebal_dates = ret.index
            _all_monthly_keys = [
                "f_revenue_m", "f_net_income_m", "f_ebitda_m", "f_equity_m",
                "f_total_assets_m", "f_net_debt_m", "f_shares_outstanding_m",
            ]
            for key in _all_monthly_keys:
                shared[key] = pd.DataFrame(index=rebal_dates, dtype=float)

        # ── Dynamic ratio computation from raw monthly inputs + raw_close ────────
        # Ratios are computed here rather than loaded from stored columns.
        # raw_close is already in shared (month-end resampled, unadjusted close).
        _shares_m = shared.get("f_shares_outstanding_m", pd.DataFrame())
        _ni_m = shared.get("f_net_income_m", pd.DataFrame())
        _equity_m = shared.get("f_equity_m", pd.DataFrame())
        _ebitda_m = shared.get("f_ebitda_m", pd.DataFrame())
        _net_debt_m = shared.get("f_net_debt_m", pd.DataFrame())
        _prices_m = shared.get("raw_close", pd.DataFrame())
        rebal_dates = ret.index

        if not _shares_m.empty and not _ni_m.empty and not _prices_m.empty:
            shared["f_pe_ratio_dyn"] = compute_pe_ratio_dynamic(_shares_m, _ni_m, _prices_m)
        else:
            shared["f_pe_ratio_dyn"] = pd.DataFrame(index=rebal_dates, dtype=float)

        if not _shares_m.empty and not _equity_m.empty and not _prices_m.empty:
            shared["f_pb_ratio_dyn"] = compute_pb_ratio_dynamic(_shares_m, _equity_m, _prices_m)
        else:
            shared["f_pb_ratio_dyn"] = pd.DataFrame(index=rebal_dates, dtype=float)

        if not _shares_m.empty and not _ebitda_m.empty and not _prices_m.empty:
            _nd = _net_debt_m if not _net_debt_m.empty else pd.DataFrame(0.0, index=_shares_m.index, columns=_shares_m.columns)
            shared["f_ev_ebitda_dyn"] = compute_ev_ebitda_dynamic(_shares_m, _ebitda_m, _nd, _prices_m)
        else:
            shared["f_ev_ebitda_dyn"] = pd.DataFrame(index=rebal_dates, dtype=float)

    return shared
