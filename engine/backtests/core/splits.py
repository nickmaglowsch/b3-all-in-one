"""Unrecorded splits, found in the prices themselves.

Lifted out of `shared_data` unchanged. It was the largest single block left in
that module and none of it is about assembling the shared frames: it is price
forensics, with its own thresholds, its own failure modes and its own
bar-for-bar characterisation suite (`tests/test_unrecorded_split_detector.py`).
The same reason `fundamentals.py` came out of the same function.

**This is B3-only, and that is a correctness property rather than a scoping
choice.** The detector fires on a persistent, calm-volume overnight repricing by
a clean ratio. On B3 that is an unrecorded grupamento. In crypto a -50% day is a
Tuesday, and letting it loose would silently rewrite price history — so
`markets.MarketSpec.detect_splits` gates the one call site, and only B3 sets it.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Common split ratios (N:1 forward splits)
_SPLIT_RATIOS = [2, 3, 4, 5, 8, 10]
_SPLIT_TOLERANCE = 0.04  # 4% tolerance for ratio matching
_VOLUME_SPIKE = 3.0      # event-day BRL volume >= 3x trailing median => crash, not split


def _find_events(
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


def detect_and_fix_unrecorded(
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

        split_events = _find_events(
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
