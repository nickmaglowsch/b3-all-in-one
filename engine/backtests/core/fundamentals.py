"""Point-in-time fundamentals, and the ratios computed from them.

Lifted out of `shared_data.build_shared_data` unchanged. It was the largest
single block in that function and none of it is about building the price frames
the rest of the module assembles — it reads a different table, on a different
calendar, with its own failure modes.

The point-in-time semantics live in `data.py` (`_query_fundamentals_pit`,
`_pit_frame`) and `pit.py`: rows are keyed on `filing_date`, a restatement only
becomes visible on its own filing date and only if it is of the newest period
known by then (`pit.latest_period_rows`), and a value whose source filing has
gone stale is blanked rather than forward-filled forever. What this module adds
is the attachment — putting those frames into the shared dict under the `f_`
names strategies read — plus, on B3, TTM EBITDA and share counts put on the
basis of the rebalance-date close.
"""

import logging

import pandas as pd

from backtests.core.data import (
    _broadcast_roots_to_tickers,
    _pit_frame,
    _query_fundamentals_pit,
    compute_ev_ebitda_dynamic,
    compute_pb_ratio_dynamic,
    compute_pe_ratio_dynamic,
    load_all_fundamentals,
    load_all_fundamentals_monthly,
)
from backtests.core.pit import (
    load_stock_actions,
    period_end_days,
    split_multiplier_frame,
    ttm_from_ytd,
)

logger = logging.getLogger(__name__)

_STALENESS_DAYS = 400


def attach(shared: dict, db_path: str, start: str, end: str, freq: str,
           ret: pd.DataFrame, market: str = "B3") -> None:
    """Add every `f_*` frame to `shared`, in place.

    `ret` supplies the rebalance calendar and the ticker universe; every frame
    lands on that calendar so a strategy can index it beside the price frames.
    """
    logger.info("Loading CVM fundamentals data (include_fundamentals=True)...")
    # Pass the ticker universe so the root-keyed fundamentals_pit frames are
    # broadcast onto full tickers (else they silently fail to join — the bug
    # that made ValueQuality never invest).
    fundamentals = load_all_fundamentals(db_path, start, end, freq=freq,
                                         tickers=ret.columns, market=market)
    for metric, df_fund in fundamentals.items():
        shared["f_" + metric] = df_fund
    logger.info(f"Added {len(fundamentals)} fundamentals DataFrames with 'f_' prefix")

    _attach_monthly(shared, db_path, start, end, ret)
    if market == "B3":
        _attach_b3_derived(shared, db_path, start, end, ret)
    _attach_ratios(shared, ret)


def _attach_monthly(shared: dict, db_path: str, start: str, end: str,
                    ret: pd.DataFrame) -> None:
    """Monthly snapshot — f_*_m (raw financials, all use '_m' suffix).

    Reads from fundamentals_monthly (pre-materialized by pipeline step 9b).
    Gracefully degrades if the table is empty or doesn't exist yet.
    """
    rebal_dates = ret.index  # use the strategy's rebalance calendar
    try:
        fundamentals_monthly = load_all_fundamentals_monthly(db_path, start, end)
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
        for key in ["f_revenue_m", "f_net_income_m", "f_ebitda_m", "f_equity_m",
                    "f_total_assets_m", "f_net_debt_m", "f_shares_outstanding_m",
                    "f_net_income_ttm_m"]:
            shared[key] = pd.DataFrame(index=rebal_dates, dtype=float)


def _attach_b3_derived(shared: dict, db_path: str, start: str, end: str,
                       ret: pd.DataFrame) -> None:
    """TTM EBITDA and split-consistent share counts, from one PIT read.

    f_ebitda_ttm: CVM EBITDA is stored year-to-date like net income, and no
    TTM is materialized for it, so it is derived here (pit.ttm_from_ytd).

    Share counts come from filings up to 400 days old (the FRE is annual);
    raw_close is the unadjusted close of the rebalance date. After a 1:10
    desdobramento in between, close x shares was a tenth of the company — P/E
    and P/B ten times "cheaper", exactly the names a value screen buys, and
    Teva's market-cap floor misapplied. f_shares_outstanding(_m) are multiplied
    by the stock actions with ex_date in (period_end of the count's filing,
    rebalance date] — sp500_b3.share_multiplier, as a frame. The anchor is
    rebuilt from fundamentals_pit with the same as-of rules; where none is found
    (or the actions cannot be read) the count is left as it was.
    """
    try:
        long = _query_fundamentals_pit(db_path, ["ebitda", "shares_outstanding"], end, "B3")
    except Exception as e:
        logger.warning(f"No TTM EBITDA / split-adjusted shares (PIT read failed): {e}")
        return
    if long.empty:
        return
    ttm = long.assign(ebitda_ttm=ttm_from_ytd(long, "ebitda"))
    shared["f_ebitda_ttm"] = _broadcast_roots_to_tickers(
        _pit_frame(ttm, "ebitda_ttm", ret.index, start, _STALENESS_DAYS), ret.columns)

    try:
        actions = load_stock_actions(db_path)
    except Exception as e:
        logger.warning(f"Share counts not split-adjusted (stock_actions unreadable): {e}")
        return
    anchor = _pit_frame(period_end_days(long, "shares_outstanding"), "shares_outstanding",
                        ret.index, start, _STALENESS_DAYS)
    mult = split_multiplier_frame(actions, _broadcast_roots_to_tickers(anchor, ret.columns))
    for key in ("f_shares_outstanding_m", "f_shares_outstanding"):
        shares = shared.get(key)
        if shares is not None and not shares.empty:
            shared[key] = shares * mult.reindex(index=shares.index,
                                                columns=shares.columns).fillna(1.0)


def _attach_ratios(shared: dict, ret: pd.DataFrame) -> None:
    """f_pe_ratio_dyn, f_pb_ratio_dyn, f_ev_ebitda_dyn from the monthly inputs.

    Computed here rather than loaded from stored columns. raw_close is the
    period-end unadjusted close; on B3 the share counts are on its basis
    (_attach_b3_derived).

    Earnings are trailing twelve months. CVM stores ITR income year-to-date
    (Q1 = 3 months, Q2 = 6, Q3 = 9), so f_net_income_m / f_ebitda_m made P/E
    ~4x too high after Q1 filings and ~2x after Q2, and the reporting calendar
    rather than the price decided who passed LowPE's 1..30 band. P/E reads the
    materialized net_income_ttm; EV/EBITDA reads f_ebitda_ttm on the cells of
    f_ebitda_m.
    """
    empty = pd.DataFrame()
    shares = shared.get("f_shares_outstanding_m", empty)
    ni_ttm = shared.get("f_net_income_ttm_m", empty)
    equity = shared.get("f_equity_m", empty)
    ebitda_m = shared.get("f_ebitda_m", empty)
    ebitda_ttm = shared.get("f_ebitda_ttm", empty)
    ebitda = (ebitda_ttm.reindex(index=ebitda_m.index, columns=ebitda_m.columns)
              if not ebitda_m.empty and not ebitda_ttm.empty else empty)
    net_debt = shared.get("f_net_debt_m", empty)
    prices = shared.get("raw_close", empty)
    blank = pd.DataFrame(index=ret.index, dtype=float)
    have = not shares.empty and not prices.empty

    shared["f_pe_ratio_dyn"] = (compute_pe_ratio_dynamic(shares, ni_ttm, prices)
                                if have and not ni_ttm.empty else blank)
    shared["f_pb_ratio_dyn"] = (compute_pb_ratio_dynamic(shares, equity, prices)
                                if have and not equity.empty else blank.copy())
    if have and not ebitda.empty:
        nd = net_debt if not net_debt.empty else pd.DataFrame(
            0.0, index=shares.index, columns=shares.columns)
        shared["f_ev_ebitda_dyn"] = compute_ev_ebitda_dynamic(shares, ebitda, nd, prices)
    else:
        shared["f_ev_ebitda_dyn"] = blank.copy()
