"""Checking B3's split factors against the prices they claim to explain.

Split out of `adjustments`, which computes factors; this decides which B3
actions are allowed to produce one. Its output is also what
`storage.replace_stock_actions` writes back, so a correction made here outlives
the run that made it — and the next run must recognise it rather than make it
again (see `_superseded_corrections`).
"""

import logging

import numpy as np
import pandas as pd

from . import config

logger = logging.getLogger(__name__)

CORRECTED = "B3_CORRECTED"

_SPLIT_TYPES = {
    config.EVENT_TYPE_STOCK_SPLIT,
    config.EVENT_TYPE_REVERSE_SPLIT,
    config.EVENT_TYPE_BONUS_SHARES,
}

# The stock_actions primary key. Two rows sharing it are one row once stored.
_TABLE_KEY = ["isin_code", "ex_date", "action_type"]


def _snap(ratio: float) -> float:
    """Snap a ratio to the nearest n or 1/n (n integer) when within 5%."""
    if ratio >= 1.5:
        n = round(ratio)
        return float(n) if n > 0 and abs(n - ratio) / ratio < 0.05 else ratio
    if 0 < ratio <= 1 / 1.5:
        n = round(1.0 / ratio)
        snapped = 1.0 / n if n > 0 else ratio
        return snapped if abs(snapped - ratio) / ratio < 0.05 else ratio
    return ratio


def _observed_ratio(grp: pd.DataFrame | None, ex_date: pd.Timestamp,
                    date_window: int) -> float | None:
    """Last close before `ex_date` over the first close on or after it, each
    within `date_window` calendar days; None when either side is missing."""
    if grp is None or len(grp) < 2:
        return None
    window = pd.Timedelta(days=date_window)
    before = grp[(grp["date"] < ex_date) & (grp["date"] >= ex_date - window)]
    after = grp[(grp["date"] >= ex_date) & (grp["date"] <= ex_date + window)]
    if before.empty or after.empty:
        return None
    close_before = before.iloc[-1]["close"]
    close_after = after.iloc[0]["close"]
    if close_before <= 0 or close_after <= 0:
        return None
    return close_before / close_after


def _correction(row, grp: pd.DataFrame | None, tolerance: float,
                date_window: int) -> float | None:
    """None to keep `row` as is, 0.0 to drop it as phantom, otherwise the
    factor the prices say it should have had."""
    factor = row["factor"]
    if row["action_type"] == config.EVENT_TYPE_BONUS_SHARES:
        expected = 1.0 + factor / 100.0
    else:
        expected = factor  # SPLIT: N>1; REVERSE_SPLIT: f<1 — both before/after
    observed = _observed_ratio(grp, pd.to_datetime(row["ex_date"]), date_window)
    if observed is None or expected <= 0:
        return None

    log_tol = np.log(1.0 + tolerance)
    if abs(np.log(observed / expected)) <= log_tol:
        return None  # factor matches reality
    if abs(np.log(observed)) <= log_tol:
        logger.warning(
            f"Dropping phantom {row['action_type']} for {row['isin_code']} on "
            f"{row['ex_date']}: B3 factor={factor} but observed price ratio="
            f"{observed:.3f} (no jump)"
        )
        return 0.0
    # Something happened, but not what B3 says — trust the prices
    corrected = _snap(observed)
    logger.warning(
        f"Correcting {row['action_type']} for {row['isin_code']} on "
        f"{row['ex_date']}: B3 factor={factor} (expected ratio {expected:.3f}) "
        f"but observed price ratio={observed:.3f} -> factor={corrected:.4f}"
    )
    return corrected


def _ex_bar_after_cum_date(row, grp: pd.DataFrame | None, tolerance: float,
                           date_window: int):
    """The bar after `row`'s date when the jump is there instead, else None.

    B3's `lastDatePrior` is the last day the shares trade *with* the right
    (data-com), so the price jump usually lands on the next bar, not on the
    stored date (prod audit 2026-11: 380 of ~420 price-confirmed B3 splits).
    Judged on the stored date those read as "no jump" and were dropped as
    phantom (DIRR 2025, BBAS 2024, SBSP 2026...).
    """
    if grp is None:
        return None
    expected = (1.0 + row["factor"] / 100.0
                if row["action_type"] == config.EVENT_TYPE_BONUS_SHARES else row["factor"])
    d = pd.to_datetime(row["ex_date"])
    later = grp[grp["date"] > d]
    if later.empty or expected <= 0:
        return None
    nxt = later.iloc[0]["date"]
    if (nxt - d).days > date_window:
        return None
    observed = _observed_ratio(grp, nxt, date_window)
    if observed is None or abs(np.log(observed / expected)) > np.log(1.0 + tolerance):
        return None
    return nxt.isoformat()[:10] if isinstance(row["ex_date"], str) else nxt.date()


def _superseded_corrections(stock_actions: pd.DataFrame, events: set) -> list:
    """Stored B3_CORRECTED rows for an event this pass has just corrected again.

    A correction can change `action_type` — the 1-for-40 reverse split that was
    really a 2:1 split comes back as STOCK_SPLIT — and `action_type` is part of
    the table key. So the run after a correction is stored, the B3 fetch puts the
    original row back *beside* the correction rather than over it, and this pass
    corrects it again. Both would be applied, squaring the ratio for every bar
    before the event; then the store collapses them into one row, the table looks
    clean, and the next run does it all again.

    The stored row is an earlier pass's reading of the same B3 row, so the fresh
    one replaces it. A stored correction whose B3 original did not come back
    (fetch skipped or failed) is not in `events` and is kept.
    """
    if not events:
        return []
    keys = zip(stock_actions["isin_code"], pd.to_datetime(stock_actions["ex_date"]))
    hit = [key in events for key in keys]
    stale = (stock_actions["source"] == CORRECTED) & pd.Series(hit, index=stock_actions.index)
    return stock_actions.index[stale].tolist()


def _detected_on(stock_actions: pd.DataFrame, events: set) -> list:
    """Rows the price detector stored for an (isin, ex_date) in `events`."""
    if not events:
        return []
    keys = zip(stock_actions["isin_code"], pd.to_datetime(stock_actions["ex_date"]))
    hit = pd.Series([key in events for key in keys], index=stock_actions.index)
    return stock_actions.index[hit & stock_actions["source"].astype(str)
                               .str.startswith("DETECTED")].tolist()


def _detected_near_b3(actions: pd.DataFrame, tolerance: float, date_window: int) -> list:
    """DETECTED rows a B3/B3_CORRECTED row on the same ISIN, at most
    `date_window` days away and with the same share ratio, already explains.

    The detector only skips a jump with a stored action in the 5 bars before it,
    and never revisits what it stored. A DETECTED row written while B3's row was
    missing (dropped as phantom before the data-com fix) survives once B3's row
    comes back a few days earlier: both applied, the ratio squared for every bar
    before the event (prod 2026-11: BRBALMACNPR2 B3 2014-04-30 / DETECTED
    2014-05-02). Dropped here every run, so the store stays clean.
    """
    ratio = actions["factor"].astype(float).where(
        actions["action_type"] != config.EVENT_TYPE_BONUS_SHARES,
        1.0 + actions["factor"].astype(float) / 100.0)
    a = actions[["isin_code", "source"]].assign(
        d=pd.to_datetime(actions["ex_date"]), r=ratio)
    det = a[a["source"].astype(str).str.startswith("DETECTED")].reset_index()
    b3 = a.loc[a["source"].isin(["B3", CORRECTED]), ["isin_code", "d", "r"]]
    pairs = det.merge(b3, on="isin_code", suffixes=("", "_b3"))
    near = ((pairs["d"] - pairs["d_b3"]).abs() <= pd.Timedelta(days=date_window)) & (
        np.abs(np.log(pairs["r"] / pairs["r_b3"])) <= np.log(1.0 + tolerance))
    return pairs.loc[near, "index"].unique().tolist()


def _unique_on_table_key(actions: pd.DataFrame) -> pd.DataFrame:
    """One row per stock_actions key, so what is applied is what gets stored:
    two B3 rows on one date can be corrected into the same key."""
    key = actions[["isin_code", "action_type"]].assign(
        ex_date=pd.to_datetime(actions["ex_date"]))
    dup = key.duplicated(subset=_TABLE_KEY, keep="last")
    return actions[~dup].reset_index(drop=True) if dup.any() else actions


def validate_split_actions_against_prices(
    stock_actions: pd.DataFrame,
    prices: pd.DataFrame,
    tolerance: float = 0.35,
    date_window: int = 7,
) -> pd.DataFrame:
    """
    Validate B3 API split-type actions against observed raw price ratios.

    B3's corporate-actions API occasionally reports factors that contradict the
    COTAHIST price series (e.g. a "1-for-100 reverse split" on a date where the
    quoted price did not move, or a 1:2 split labeled as a 1-for-40 reverse
    split). Applying such factors corrupts adj_close with fake ±99% jumps.

    COTAHIST prices are treated as the source of truth. For each B3-source
    SPLIT/REVERSE_SPLIT/BONUS action, compare the expected before/after close
    ratio implied by the factor with the observed ratio around the ex_date:

    - match within tolerance      -> keep as-is
    - observed ratio ~= 1         -> drop (phantom action; prices never moved)
    - jump on the next bar instead -> move ex_date there (B3 dates are data-com)
    - observed matches a different
      ratio                       -> replace factor with the observed ratio
                                     (snapped to the nearest simple ratio when
                                     within 5%), source='B3_CORRECTED'

    Idempotent across runs: a stored correction of an event corrected again
    here is dropped in favour of the fresh one (`_superseded_corrections`), and
    the result is unique on the table key.

    Args:
        stock_actions: stock actions (isin_code, ex_date, action_type, factor, source).
        prices: per-share price data (isin_code, date, close).
        tolerance: relative tolerance between observed and expected ratio.
        date_window: max calendar days to search for closes around the ex_date.

    Returns:
        stock_actions with phantom rows dropped and wrong factors corrected.
    """
    if stock_actions.empty or prices.empty:
        return stock_actions

    check_mask = (stock_actions["source"] == "B3") & stock_actions[
        "action_type"
    ].isin(_SPLIT_TYPES)
    if not check_mask.any():
        return stock_actions

    prices_by_isin = {
        isin: grp.sort_values("date")
        for isin, grp in prices[["isin_code", "date", "close"]]
        .assign(date=lambda d: pd.to_datetime(d["date"]))
        .groupby("isin_code")
    }

    result = stock_actions.copy()
    phantom, corrected_events, n_corrected = [], set(), 0
    shifted = set()
    for idx, row in stock_actions[check_mask].iterrows():
        grp = prices_by_isin.get(row["isin_code"])
        corrected = _correction(row, grp, tolerance, date_window)
        if corrected is None:
            continue
        ex_bar = _ex_bar_after_cum_date(row, grp, tolerance, date_window)
        if ex_bar is not None:
            result.loc[idx, "ex_date"] = ex_bar
            shifted.add((row["isin_code"], pd.to_datetime(ex_bar)))
            continue
        if corrected == 0.0:
            phantom.append(idx)
            continue
        result.loc[idx, "factor"] = corrected
        result.loc[idx, "action_type"] = (
            config.EVENT_TYPE_STOCK_SPLIT
            if corrected > 1
            else config.EVENT_TYPE_REVERSE_SPLIT
        )
        result.loc[idx, "source"] = CORRECTED
        corrected_events.add((row["isin_code"], pd.to_datetime(row["ex_date"])))
        n_corrected += 1

    superseded = _superseded_corrections(stock_actions, corrected_events)
    # the detector's reading of a jump B3 now explains would apply it twice
    superseded += _detected_on(stock_actions, shifted)
    superseded += _detected_near_b3(result.drop(index=phantom + superseded),
                                    tolerance, date_window)
    if phantom or n_corrected or shifted:
        logger.info(
            f"Split validation: moved {len(shifted)} to the ex bar after B3's "
            f"data-com, dropped {len(phantom)} phantom, corrected "
            f"{n_corrected} mismatched actions against observed prices, "
            f"superseded {len(superseded)} stored corrections of the same events"
        )
    if phantom or superseded:
        result = result.drop(index=phantom + superseded).reset_index(drop=True)
    return _unique_on_table_key(result)
