"""Backward split and dividend factors for a US symbol.

The free daily series is **unadjusted** — the adjusted endpoint is premium — so
`adj_close = close` would fabricate a -50% day across every 2:1 split and a
+900% day across every 1:10 reverse. Worse, `MARKETS["US"].glitch_down = -0.45`
would then mask the name for the whole lookback around a *real* corporate action,
so the damage would be quiet. The factors are therefore built here, from the
vendor's separate splits and dividends endpoints.

The conventions are `b3_pipeline.adjustments`', deliberately, and
`test_us_adjust.py` asserts the two produce identical factor arrays on the same
events. They are not shared code: `adjustments` is keyed on `isin_code`, takes
B3-shaped frames, and sits at 914 lines against a 914-line ceiling in
`quality_baseline.json`. Extracting its core is a worthwhile change and a
different one.

`None` and an empty frame mean different things here, and the whole honesty of
the US `adj_close` rests on the difference. An empty frame is the vendor saying
"no splits on record", which is a fact and lets the series be called adjusted.
`None` is the vendor refusing to answer, which is not. Crypto's
`adj_close = close` is honest because spot pairs have no corporate actions; the
same assignment for an equity is only honest once someone has asked.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# From adjustments.py, and for the same reason: a cumulative factor beyond this
# is bad vendor data, not a corporate action.
_MAX_CUMULATIVE = 100_000.0
_MIN_CUMULATIVE = 1.0 / 100_000.0

# A distribution at or above this share of the previous close cannot be carried
# multiplicatively — the factor collapses prior history toward zero and invents
# a huge return at the ex-date. adjustments.py skips these too, and names a real
# case (a R$19 payout on a R$5 stock).
_PATHOLOGICAL_YIELD = 0.90

UNADJUSTED = "unadjusted"
SPLIT_ONLY = "split_only"
TOTAL_RETURN = "total_return"


def _backward_cumulative(ex_dates: np.ndarray, factors: np.ndarray,
                         price_dates: np.ndarray) -> np.ndarray:
    """Clamped product of the factors of every event strictly after each date.

    `side="right"` is what makes it strict: the raw price already trades ex on
    the ex-date itself, so the factor must not apply there.
    """
    suffix = np.ones(len(factors) + 1)
    for i in range(len(factors) - 1, -1, -1):
        suffix[i] = min(_MAX_CUMULATIVE, max(_MIN_CUMULATIVE,
                                             factors[i] * suffix[i + 1]))
    return suffix[np.searchsorted(ex_dates, price_dates, side="right")]


def split_factors(dates: pd.Series, events: pd.DataFrame) -> np.ndarray:
    """Backward cumulative split factor for each date.

    `factor[d]` is the product of the ratios of every split whose ex-date is
    strictly after `d`.
    """
    price_dates = pd.to_datetime(dates).values.astype("datetime64[ns]")
    if events is None or events.empty:
        return np.ones(len(price_dates))

    events = events.dropna(subset=["ex_date"]).sort_values("ex_date")
    ex = pd.to_datetime(events["ex_date"]).values.astype("datetime64[ns]")
    return _backward_cumulative(ex, events["ratio"].values.astype(float), price_dates)


def dividend_factors(dates: pd.Series, closes: pd.Series,
                     events: pd.DataFrame) -> np.ndarray:
    """Backward cumulative dividend factor for each date.

    Yahoo's convention: each event contributes `1 - amount/prev_close`, where
    `prev_close` is the **raw** close on the last session strictly before the
    ex-date. Raw and not adjusted, because the amount the vendor reports is the
    historical per-share figure and the two have to be measured in the same
    money.
    """
    price_dates = pd.to_datetime(dates).values.astype("datetime64[ns]")
    raw = np.asarray(closes, dtype=float)
    if events is None or events.empty:
        return np.ones(len(price_dates))

    events = events.dropna(subset=["ex_date"]).sort_values("ex_date")
    per_event = []
    for _, row in events.iterrows():
        ex = np.datetime64(pd.Timestamp(row["ex_date"]), "ns")
        pos = int(np.searchsorted(price_dates, ex, side="left")) - 1
        if pos < 0:
            continue
        prev_close = raw[pos]
        amount = float(row["amount"])
        if prev_close <= 0 or amount <= 0:
            continue
        if amount >= _PATHOLOGICAL_YIELD * prev_close:
            logger.warning(
                f"Skipping a distribution of {amount} against a previous close "
                f"of {prev_close} on {pd.Timestamp(ex).date()}: it cannot be "
                "carried multiplicatively without inventing a return.")
            continue
        per_event.append((ex, 1.0 - amount / prev_close))

    if not per_event:
        return np.ones(len(price_dates))

    return _backward_cumulative(
        np.array([e for e, _ in per_event], dtype="datetime64[ns]"),
        np.array([f for _, f in per_event], dtype=float), price_dates)


def apply(bars: pd.DataFrame, splits, dividends) -> tuple:
    """Add `split_adj_close` and `adj_close`, and say what they actually are.

    Returns `(bars, adjust_state)`. The state is a receipt, not a plan: it
    records which endpoints answered for this symbol, so `/market/status` and a
    backtest's coverage block can report the truth per symbol instead of the
    product claiming a total-return series it does not have.

    Without splits nothing is adjusted at all, dividends included. A dividend
    factor laid over a series that still has its splits in it is not a partial
    improvement — it is a third wrong number.
    """
    out = bars.copy()
    if splits is None:
        out["split_adj_close"] = out["close"].astype(float)
        out["adj_close"] = out["close"].astype(float)
        return out, UNADJUSTED

    out["split_adj_close"] = (out["close"].astype(float)
                              * split_factors(out["date"], splits))
    if dividends is None:
        out["adj_close"] = out["split_adj_close"]
        return out, SPLIT_ONLY

    out["adj_close"] = (out["split_adj_close"]
                        * dividend_factors(out["date"], out["close"], dividends))
    return out, TOTAL_RETURN
