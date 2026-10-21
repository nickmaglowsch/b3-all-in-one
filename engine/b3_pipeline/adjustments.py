"""
Compute split and dividend adjustments for price data.

Implements:
1. Split adjustment factors from B3 official data (backward cumulative)
2. Dividend/JCP adjustment factors (Yahoo-style backward cumulative)
"""

import logging
from datetime import date, datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from . import config
from .split_validation import validate_split_actions_against_prices

logger = logging.getLogger(__name__)

# Sanity bounds for split adjustment factors.
# Individual event: allow up to 1000:1 splits or 1:1000 reverse splits.
# Anything beyond this is almost certainly bad data from B3's API.
_MAX_INDIVIDUAL_SPLIT_FACTOR = 1000.0
_MIN_INDIVIDUAL_SPLIT_FACTOR = 1.0 / 1000.0

# Cumulative factor: allow up to 100,000x total adjustment across all events.
_MAX_CUMULATIVE_FACTOR = 100_000.0
_MIN_CUMULATIVE_FACTOR = 1.0 / 100_000.0


def _normalize_date(val) -> Optional[date]:
    """Convert various date formats to datetime.date object."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        try:
            if "T" in val:
                return datetime.fromisoformat(val.replace("Z", "+00:00")).date()
            return datetime.strptime(val[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _normalize_date_series(s: pd.Series) -> pd.Series:
    """_normalize_date applied to a whole column at once.

    The scalar version ran per row through .apply() over the entire prices table
    — millions of Python calls per pipeline run, in two places.

    The string case collapses to one parse: the scalar version's two branches
    (fromisoformat for anything containing a "T", strptime over the first ten
    characters otherwise) always agreed, because a timezone suffix never shifts
    the wall-clock date that .date() returns. Pinned by
    test_iso_string_with_a_timezone_keeps_its_wall_clock_date.

    Anything whose element types are not a clean set of str, or of
    date/datetime/Timestamp, falls back to the scalar path rather than letting
    pandas guess a format — a column of "15/01/2020" must keep parsing as None,
    not become a day-first date.
    """
    if s.empty:
        return pd.Series([], dtype=object, index=s.index)

    present = s.dropna()
    if present.empty:
        return pd.Series([None] * len(s), dtype=object, index=s.index)

    # infer_dtype is a C-level pass; mapping type() over the column would put a
    # Python call back on every row, which is the whole cost being removed here.
    kind = pd.api.types.infer_dtype(present, skipna=True)

    if kind == "date":
        # Postgres hands back datetime.date, which is already the return type —
        # the scalar version passed these straight through. Routing them via
        # to_datetime and back measured *slower* than the .apply() it replaced
        # (46ms vs 26ms over 300k rows), so the only work left is nulls -> None.
        return s.where(s.notna(), None).astype(object)

    if kind == "string":
        # SQLite's path, and the one that actually hurt: 687ms -> 57ms over 300k.
        parsed = pd.to_datetime(
            present.str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
    elif kind in ("datetime", "datetime64"):
        parsed = pd.to_datetime(present, errors="coerce")
    else:
        return s.apply(_normalize_date)

    out = pd.Series([None] * len(s), dtype=object, index=s.index)
    good = parsed.notna()
    out.loc[parsed.index[good]] = parsed[good].dt.date
    return out


def convert_stock_actions_to_splits(stock_actions: pd.DataFrame) -> pd.DataFrame:
    """
    Convert B3 stock actions to the format needed for split adjustments.

    B3 factors:
    - STOCK_SPLIT (DESDOBRAMENTO): factor > 1 means new_shares = old_shares * factor
      -> split_factor = 1/factor (e.g., 100x split -> factor=0.01)
    - REVERSE_SPLIT (GRUPAMENTO): factor < 1 means new_shares = old_shares * factor
      -> split_factor = 1/factor (e.g., 0.01 reverse -> factor=100)
    - BONUS_SHARES (BONIFICACAO): factor represents bonus ratio
      -> split_factor = 1/(1+factor) (e.g., 33.33% bonus -> factor=0.75)

    The split_factor represents: old_shares / new_shares
    - split_factor < 1: price increases (stock split)
    - split_factor > 1: price decreases (reverse split)

    Args:
        stock_actions: DataFrame from B3 with columns [isin_code, ex_date, action_type, factor]

    Returns:
        DataFrame with columns [isin_code, ex_date, split_factor, description]
    """
    if stock_actions.empty:
        return pd.DataFrame(
            columns=["isin_code", "ex_date", "split_factor", "description"]
        )

    splits = []
    for _, row in stock_actions.iterrows():
        isin_code = row["isin_code"]
        ex_date = _normalize_date(row["ex_date"])
        action_type = row["action_type"]
        b3_factor = row["factor"]

        if ex_date is None:
            continue

        if action_type == config.EVENT_TYPE_STOCK_SPLIT:
            split_factor = 1.0 / b3_factor
            description = f"Split {b3_factor}:1"
        elif action_type == config.EVENT_TYPE_REVERSE_SPLIT:
            split_factor = 1.0 / b3_factor
            description = (
                f"Reverse split 1:{1 / b3_factor:.0f}"
                if b3_factor < 1
                else f"Reverse split {1 / b3_factor:.0f}:1"
            )
        elif action_type == config.EVENT_TYPE_BONUS_SHARES:
            split_factor = 1.0 / (1.0 + b3_factor / 100.0)
            description = f"Bonus {b3_factor}%"
        else:
            continue

        # Sanity check: skip obviously bad factors from B3's API
        if split_factor > _MAX_INDIVIDUAL_SPLIT_FACTOR or split_factor < _MIN_INDIVIDUAL_SPLIT_FACTOR:
            logger.warning(
                f"Skipping extreme split factor for {isin_code} on {ex_date}: "
                f"{description} (factor={split_factor:.6f})"
            )
            continue

        splits.append(
            {
                "isin_code": isin_code,
                "ex_date": ex_date,
                "split_factor": round(split_factor, 10),
                "description": description,
            }
        )

    if splits:
        return pd.DataFrame(splits)
    return pd.DataFrame(columns=["isin_code", "ex_date", "split_factor", "description"])


def _rows_by_isin(frame: pd.DataFrame) -> dict:
    """`{isin_code: row positions}` for the whole frame, in one pass.

    Both passes want one ISIN's rows out of a table holding every ISIN.
    `frame["isin_code"] == code` answers that but scans the whole table once per
    corporate action — and so did each of the split pass's four
    `frame.loc[mask, col] =` writes, six whole-table passes per action on the
    largest frame the pipeline holds. Positions, not index labels: `prices` comes
    off a chunked read whose index is whatever the reader left.
    """
    return frame.groupby("isin_code", sort=False).indices


def compute_split_adjustment_factors(
    prices: pd.DataFrame, splits: pd.DataFrame
) -> pd.DataFrame:
    """
    Compute backward cumulative split adjustment factors based on ISIN codes.

    For each ISIN:
    1. Sort splits by date ascending, build suffix-product array
    2. Use np.searchsorted to assign each price row its factor in O(n log m)
    3. Apply to open, high, low, close via vectorized column writes
    The arithmetic was always the cheap half; see `_rows_by_isin` for the other.

    Args:
        prices: DataFrame with raw price data
        splits: DataFrame with splits (from B3 stock_actions)

    Returns:
        DataFrame with split-adjusted columns added
    """
    result = prices.copy()

    # The OHLC columns as plain arrays. Writing into one of these is the size of
    # one ISIN's rows; writing through a boolean mask was the size of the table.
    raw = {c: result[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close")}
    adjusted = {c: v.copy() for c, v in raw.items()}
    for col, out in adjusted.items():
        result[f"split_adj_{col}"] = out

    if splits.empty:
        logger.info("No splits to apply")
        return result

    logger.info(f"Applying {len(splits)} split adjustments...")

    # Pre-normalize date column once for all ISINs
    date_ts = pd.to_datetime(result["date"], errors="coerce").to_numpy("datetime64[ns]")

    price_rows, split_rows = _rows_by_isin(result), _rows_by_isin(splits)

    for isin_code, split_pos in split_rows.items():
        price_pos = price_rows.get(isin_code)
        if price_pos is None or len(price_pos) == 0:
            continue

        isin_splits = splits.take(split_pos).dropna(subset=["ex_date"]) \
                            .sort_values("ex_date", ascending=True)

        if isin_splits.empty:
            continue

        split_dates = pd.to_datetime(isin_splits["ex_date"].values, errors="coerce").astype("datetime64[ns]")
        split_factors = isin_splits["split_factor"].values.astype(float)
        n_splits = len(split_factors)

        # Build suffix-product array: suffix[i] = product of split_factors[i:]
        # suffix[n_splits] = 1.0 (no splits remaining)
        suffix = np.ones(n_splits + 1)
        for i in range(n_splits - 1, -1, -1):
            raw_factor = split_factors[i] * suffix[i + 1]
            suffix[i] = max(_MIN_CUMULATIVE_FACTOR, min(_MAX_CUMULATIVE_FACTOR, raw_factor))

        # For each price date, find first split index where split_date > price_date.
        # side='right' excludes the ex_date itself: the raw price already trades
        # ex (halved) ON the ex_date, so the factor applies only to earlier dates.
        indices = np.searchsorted(split_dates, date_ts[price_pos], side="right")
        factors = suffix[indices]

        for col, out in adjusted.items():
            out[price_pos] = raw[col][price_pos] * factors

    for col, out in adjusted.items():
        result[f"split_adj_{col}"] = out
    logger.info("Split adjustments applied")
    return result


def compute_dividend_adjustment_factors(
    prices: pd.DataFrame, corporate_actions: pd.DataFrame
) -> pd.DataFrame:
    """
    Compute Yahoo-style backward cumulative dividend adjustment factors based on ISIN.

    For each dividend/JCP event:
    1. Get previous close (trading day before ex_date)
    2. factor_on_exdate = 1 - (dividend_amount / prev_close)
    3. Build cumulative product backward from most recent to oldest
    4. adj_close = split_adj_close * cumulative_factor

    Note: Only adjusts close price (adj_close), not open/high/low.

    Same shape as `compute_split_adjustment_factors`. See `_rows_by_isin`.

    Args:
        prices: DataFrame with split-adjusted prices
        corporate_actions: DataFrame with dividend/JCP events

    Returns:
        DataFrame with adj_close column added
    """
    result = prices.copy()
    result["adj_close"] = result["split_adj_close"].copy()

    if corporate_actions.empty:
        logger.info("No corporate actions to apply")
        return result

    dividend_types = [
        config.EVENT_TYPE_CASH_DIVIDEND,
        config.EVENT_TYPE_JCP,
        "Dividendo",
        "JCP",
        "Rend. Tributado",
    ]
    dividend_actions = corporate_actions[
        corporate_actions["event_type"].isin(dividend_types)
    ].copy()

    if dividend_actions.empty:
        logger.info("No dividend/JCP events to apply")
        return result

    logger.info(
        f"Applying dividend/JCP adjustments ({len(dividend_actions)} events)..."
    )

    # Pre-normalize dates once for all ISINs
    date_ts = pd.to_datetime(result["date"], errors="coerce").to_numpy("datetime64[ns]")
    event_ts = pd.to_datetime(
        dividend_actions["event_date"], errors="coerce"
    ).to_numpy("datetime64[ns]")
    event_values = dividend_actions["value"].to_numpy()

    price_rows = _rows_by_isin(result)
    dividend_rows = _rows_by_isin(dividend_actions)
    n_isins = len(dividend_rows)

    close = result["close"].to_numpy(dtype=float)
    adj_close = result["split_adj_close"].to_numpy(dtype=float, copy=True)

    for i, (isin_code, div_pos) in enumerate(dividend_rows.items(), 1):
        if i % 100 == 0 or i == n_isins:
            logger.info(f"Applying dividend adjustments: {i}/{n_isins}")

        price_pos = price_rows.get(isin_code)
        if price_pos is None or len(price_pos) == 0:
            continue

        # The bars in date order: "the close before the ex-date" is a
        # searchsorted, and `prices` arrives in whatever order the query left.
        price_pos = price_pos[np.argsort(date_ts[price_pos], kind="stable")]
        price_dates = date_ts[price_pos]
        price_closes = close[price_pos]

        div_pos = div_pos[~np.isnat(event_ts[div_pos])]   # undated: dropped
        if len(div_pos) == 0:
            continue

        div_dates, div_factors = [], []
        for ex_date, div_amount in zip(event_ts[div_pos], event_values[div_pos]):
            if div_amount is None or div_amount <= 0:
                continue

            # Find last price before ex_date via searchsorted (avoids boolean mask)
            pos = int(np.searchsorted(price_dates, ex_date, side="left")) - 1
            if pos < 0:
                continue

            # IMPORTANT: We must use the RAW close price to calculate the yield factor,
            # because B3 dividend amounts are raw historical amounts (not split-adjusted).
            prev_close = price_closes[pos]

            if prev_close <= 0:
                continue

            if div_amount >= 0.90 * prev_close:
                # A distribution >= ~price cannot be represented multiplicatively
                # (factor <= 0.1 crushes all prior history toward zero, creating
                # fake +30x "returns" at the ex-date). Skip and keep price-based
                # history for this event (e.g. BRPR3's R$19 payout on a R$5 stock).
                logger.warning(
                    f"Skipping pathological dividend for {isin_code} on "
                    f"{pd.Timestamp(ex_date).date()}: value={div_amount} vs "
                    f"prev_close={prev_close} (>=90% of price)"
                )
                continue

            factor = 1.0 - (div_amount / prev_close)
            factor = min(1.0, factor)

            div_dates.append(ex_date)
            div_factors.append(factor)

        if not div_factors:
            continue

        # A bar's factor is the product of every dividend strictly after it.
        # The backward sweep this replaces walked one Python step per *bar* —
        # millions of them over the plane — to compute the same suffix product.
        # `cumprod` over the reversed factors performs the identical
        # multiplications in the identical order, so the floats are unchanged.
        dates_arr = np.array(div_dates, dtype="datetime64[ns]")
        order = np.argsort(dates_arr, kind="stable")
        dates_arr = dates_arr[order]
        suffix = np.ones(len(order) + 1)
        suffix[:-1] = np.cumprod(
            np.asarray(div_factors, dtype=float)[order][::-1])[::-1]
        idx = np.searchsorted(dates_arr, price_dates, side="right")
        adj_close[price_pos] = adj_close[price_pos] * suffix[idx]

    result["adj_close"] = adj_close
    logger.info("Dividend/JCP adjustments applied")
    return result


def detect_splits_from_prices(
    prices: pd.DataFrame,
    existing_stock_actions: pd.DataFrame,
    detect_nonstandard: bool = False,
) -> pd.DataFrame:
    """
    Detect missing stock splits from price data by looking for overnight price jumps.

    This is a fallback heuristic for splits not returned by B3's API. It compares
    consecutive-day close prices and flags jumps matching common split ratios.

    Algorithm:
    1. For each ISIN, compute consecutive-day price ratios from the close column.
    2. Identify jumps where ratio > SPLIT_DETECTION_THRESHOLD_HIGH (e.g., 1.8) or
       ratio < SPLIT_DETECTION_THRESHOLD_LOW (e.g., 0.55) within a 5-trading-day window.
    3. Skip if a stock_actions entry already exists for that ISIN and date range.
    4. Skip if a quotation_factor transition on that date explains the jump.
    5. Attempt to match remaining jumps against common split ratios with 8% tolerance.
    6. Return matched splits as stock_actions with source='DETECTED'.

    Args:
        prices: DataFrame with raw price data (must include isin_code, date, close,
                and optionally quotation_factor columns).
        existing_stock_actions: DataFrame of already-known stock actions (to avoid
                                duplicates).

    Returns:
        DataFrame of newly detected stock actions with source='DETECTED'.
        Columns: [isin_code, ex_date, action_type, factor, source]
    """
    # Common split ratios to try: (N, direction) where N is the integer multiple
    _common_ratios = [2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 50, 100]
    _tolerance = 0.08  # 8% tolerance -- wider than backtest's 4% for pipeline auditing

    detected = []

    if prices.empty:
        return pd.DataFrame(
            columns=["isin_code", "ex_date", "action_type", "factor", "source"]
        )

    # Normalize date column (vectorised — this runs over the whole price table)
    prices = prices.copy()
    prices["date"] = _normalize_date_series(prices["date"])

    existing_keys: set = set()
    if not existing_stock_actions.empty:
        for _, row in existing_stock_actions.iterrows():
            ex = _normalize_date(row["ex_date"])
            if ex is not None:
                existing_keys.add((row["isin_code"], ex))

    for isin_code, group in prices.groupby("isin_code"):
        group = group.sort_values("date").reset_index(drop=True)

        if len(group) < 2:
            continue

        closes = group["close"].values
        dates = group["date"].values
        factors = group["quotation_factor"].values if "quotation_factor" in group.columns else None

        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            curr_close = closes[i]

            if prev_close <= 0 or curr_close <= 0:
                continue

            ratio = curr_close / prev_close

            # Only flag large jumps
            if config.SPLIT_DETECTION_THRESHOLD_LOW <= ratio <= config.SPLIT_DETECTION_THRESHOLD_HIGH:
                continue

            jump_date = dates[i]

            # Skip if existing stock_action already covers this date (within 5-day window)
            already_recorded = False
            for lookback_i in range(max(0, i - 5), i + 1):
                candidate_date = dates[lookback_i]
                if (isin_code, candidate_date) in existing_keys:
                    already_recorded = True
                    break
            if already_recorded:
                continue

            # Skip if this is a quotation_factor transition that explains the jump.
            # After normalization, a fatcot transition produces a continuous series --
            # prices are already per-share on both sides. However, if the fatcot
            # column is not yet normalized (pre-normalization pipeline), we check
            # whether the fatcot changed on this date.
            if factors is not None:
                prev_factor = factors[i - 1]
                curr_factor = factors[i]
                if prev_factor != curr_factor and prev_factor > 0 and curr_factor > 0:
                    # The "jump" is accounted for by the quotation factor change.
                    # Since the parser already normalizes prices by dividing by
                    # quotation_factor, if prices are still jumping here it means
                    # something else is going on. We skip if the ratio of factors
                    # explains the price ratio within tolerance.
                    factor_ratio = curr_factor / prev_factor
                    if abs(ratio - (1.0 / factor_ratio)) / max(abs(1.0 / factor_ratio), 0.001) < _tolerance:
                        continue
                    # Also skip if prices are already per-share and the fatcot
                    # transition just changes the metadata (no jump expected)
                    if abs(ratio - 1.0) < _tolerance:
                        continue

            # Try to match against common forward split ratios (close drops by 1/N)
            matched = False
            for n in _common_ratios:
                # Forward split: close drops to 1/N of previous
                target_forward = 1.0 / n
                if abs(ratio - target_forward) / target_forward < _tolerance:
                    detected.append({
                        "isin_code": isin_code,
                        "ex_date": jump_date,
                        "action_type": config.EVENT_TYPE_STOCK_SPLIT,
                        "factor": float(n),
                        "source": "DETECTED",
                    })
                    matched = True
                    logger.info(
                        f"Detected forward split {n}:1 for {isin_code} on {jump_date} "
                        f"(ratio={ratio:.4f})"
                    )
                    break

                # Reverse split: close rises to N times previous
                target_reverse = float(n)
                if abs(ratio - target_reverse) / target_reverse < _tolerance:
                    detected.append({
                        "isin_code": isin_code,
                        "ex_date": jump_date,
                        "action_type": config.EVENT_TYPE_REVERSE_SPLIT,
                        "factor": 1.0 / float(n),
                        "source": "DETECTED",
                    })
                    matched = True
                    logger.info(
                        f"Detected reverse split 1:{n} for {isin_code} on {jump_date} "
                        f"(ratio={ratio:.4f})"
                    )
                    break

            if not matched and detect_nonstandard:
                # Use raw ratio as factor for non-standard splits.
                # Convention matches standard detection:
                #   STOCK_SPLIT factor = split multiple (e.g., 2.37 for a 2.37:1 split)
                #   REVERSE_SPLIT factor = 1/multiple (e.g., 0.42 for a 1:2.37 reverse)
                if ratio < 1.0:
                    # Price dropped -> forward split
                    split_multiple = 1.0 / ratio
                    action_type = config.EVENT_TYPE_STOCK_SPLIT
                    factor = split_multiple
                    label = f"forward {split_multiple:.2f}:1"
                else:
                    # Price rose -> reverse split
                    action_type = config.EVENT_TYPE_REVERSE_SPLIT
                    factor = 1.0 / ratio
                    label = f"reverse 1:{ratio:.2f}"

                detected.append({
                    "isin_code": isin_code,
                    "ex_date": jump_date,
                    "action_type": action_type,
                    "factor": float(factor),
                    "source": "DETECTED_NONSTANDARD",
                })
                matched = True
                logger.info(
                    f"Detected nonstandard split {label} for {isin_code} on {jump_date} "
                    f"(ratio={ratio:.4f})"
                )

            if not matched and (ratio > 3.0 or ratio < 0.33):
                logger.warning(
                    f"Large unmatched price jump for {isin_code} on {jump_date}: "
                    f"ratio={ratio:.4f} (prev={prev_close:.4f}, curr={curr_close:.4f}). "
                    f"Manual review recommended."
                )

    if detected:
        df = pd.DataFrame(detected)
        df = df.drop_duplicates(subset=["isin_code", "ex_date", "action_type"])
        logger.info(
            f"Split detection: found {len(df)} new potential splits from price data"
        )
        return df

    return pd.DataFrame(
        columns=["isin_code", "ex_date", "action_type", "factor", "source"]
    )


def filter_fatcot_redundant_splits(
    stock_actions: pd.DataFrame,
    prices: pd.DataFrame,
    tolerance: float = 0.15,
    date_window: int = 5,
) -> pd.DataFrame:
    """
    Remove B3 API stock_actions that duplicate a quotation_factor transition.

    When a stock transitions from lot-based pricing (fatcot=1000) to per-share
    pricing (fatcot=1), B3's API sometimes also reports a DESDOBRAMENTO (split)
    for the same date and ratio. Since the parser already normalizes prices by
    dividing by quotation_factor, applying the API split would double-adjust.

    This function identifies and removes such redundant splits by checking whether
    a fatcot transition on or near the ex_date explains the reported split factor.

    Args:
        stock_actions: DataFrame of stock actions (must include source, isin_code,
                       ex_date, factor, action_type columns).
        prices: DataFrame of price data (must include isin_code, date,
                quotation_factor columns).
        tolerance: Relative tolerance for matching the fatcot ratio to the split
                   factor (default 15%).
        date_window: Number of trading days around the ex_date to search for a
                     fatcot transition (default 5).

    Returns:
        Filtered stock_actions DataFrame with redundant entries removed.
    """
    if stock_actions.empty or prices.empty:
        return stock_actions

    if "quotation_factor" not in prices.columns:
        return stock_actions

    # Only check B3 API splits (not DETECTED ones)
    b3_mask = stock_actions["source"] == "B3"
    if not b3_mask.any():
        return stock_actions

    # Only the three columns this needs, normalised once. Copying the full price
    # frame duplicated fourteen columns of the largest table the pipeline holds.
    slim = prices[["isin_code", "date", "quotation_factor"]].copy()
    slim["date"] = _normalize_date_series(slim["date"])

    # Group once. This used to re-filter and re-sort the entire price frame for
    # every B3 action — O(actions x rows), the pipeline's worst scaling. The
    # sibling check (split_validation.validate_split_actions_against_prices)
    # already builds exactly this dict; the two are now consistent.
    prices_by_isin = {
        isin: grp.sort_values("date")
        for isin, grp in slim.groupby("isin_code", sort=False)
    }

    redundant_indices = []

    for idx, row in stock_actions[b3_mask].iterrows():
        isin = row["isin_code"]
        ex_date = _normalize_date(row["ex_date"])
        b3_factor = row["factor"]
        action_type = row["action_type"]

        if ex_date is None:
            continue

        isin_prices = prices_by_isin.get(isin)
        if isin_prices is None or len(isin_prices) < 2:
            continue

        # Look for a fatcot transition within the date window
        dates = isin_prices["date"].values
        factors = isin_prices["quotation_factor"].values

        for i in range(1, len(dates)):
            d = dates[i]
            if d is None:
                continue

            day_diff = abs((d - ex_date).days) if hasattr(d, 'days') else abs((pd.Timestamp(d) - pd.Timestamp(ex_date)).days)
            if day_diff > date_window:
                continue

            prev_fatcot = factors[i - 1]
            curr_fatcot = factors[i]

            if prev_fatcot == curr_fatcot or prev_fatcot <= 0 or curr_fatcot <= 0:
                continue

            # The fatcot ratio: e.g., 1000->1 gives fatcot_ratio = 1000
            fatcot_ratio = prev_fatcot / curr_fatcot

            # For a STOCK_SPLIT with factor N, the expected fatcot ratio is N
            # For a REVERSE_SPLIT with factor < 1, the expected fatcot ratio is 1/factor
            if action_type == config.EVENT_TYPE_STOCK_SPLIT:
                expected_ratio = b3_factor
            elif action_type == config.EVENT_TYPE_REVERSE_SPLIT:
                expected_ratio = 1.0 / b3_factor if b3_factor > 0 else 0
            else:
                continue

            if expected_ratio <= 0:
                continue

            rel_diff = abs(fatcot_ratio - expected_ratio) / expected_ratio
            if rel_diff < tolerance:
                redundant_indices.append(idx)
                logger.info(
                    f"Filtering FATCOT_REDUNDANT split for {isin} on {ex_date}: "
                    f"B3 factor={b3_factor}, fatcot transition={prev_fatcot}->{curr_fatcot} "
                    f"(ratio={fatcot_ratio:.1f}, expected={expected_ratio:.1f})"
                )
                break  # Found the matching transition, no need to check more dates

    if redundant_indices:
        logger.info(
            f"Filtered {len(redundant_indices)} FATCOT_REDUNDANT splits "
            f"(would have caused double-adjustment)"
        )
        return stock_actions.drop(index=redundant_indices).reset_index(drop=True)

    return stock_actions


def compute_all_adjustments(
    prices: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    stock_actions: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Compute all adjustments using B3 official data.

    Args:
        prices: Raw price DataFrame
        corporate_actions: Corporate actions DataFrame (dividends, JCP)
        stock_actions: Stock actions DataFrame (splits, reverse splits, bonuses)

    Returns:
        Tuple of (adjusted_prices, splits, validated_stock_actions).
        validated_stock_actions is the input with fatcot-redundant/phantom rows
        dropped and mismatched factors corrected — callers should persist it so
        downstream consumers (e.g. share-count adjustment) see the same truth.
    """
    logger.info("Starting adjustment computation...")

    stock_actions = filter_fatcot_redundant_splits(stock_actions, prices)

    # Drop/correct B3 API actions that contradict observed COTAHIST price ratios
    stock_actions = validate_split_actions_against_prices(stock_actions, prices)

    splits = convert_stock_actions_to_splits(stock_actions)
    logger.info(f"Converted {len(stock_actions)} stock actions to {len(splits)} splits")

    prices = compute_split_adjustment_factors(prices, splits)

    prices = compute_dividend_adjustment_factors(prices, corporate_actions)

    # Final sanity check: cap adj_close / close ratio to prevent extreme values
    # from surviving through to backtests
    close_vals = prices["close"].replace(0, np.nan)
    ratio = prices["adj_close"] / close_vals
    extreme_mask = (ratio.abs() > _MAX_CUMULATIVE_FACTOR) | ratio.isna()
    n_extreme = extreme_mask.sum()
    if n_extreme > 0:
        logger.warning(
            f"Clamping {n_extreme} rows with extreme adj_close/close ratio "
            f"(>{_MAX_CUMULATIVE_FACTOR}x)"
        )
        # Fall back to split_adj_close for extreme rows
        prices.loc[extreme_mask, "adj_close"] = prices.loc[
            extreme_mask, "split_adj_close"
        ]

    logger.info("Adjustment computation complete")

    return prices, splits, stock_actions
