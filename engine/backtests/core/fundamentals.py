"""Point-in-time fundamentals, and the ratios computed from them.

Lifted out of `shared_data.build_shared_data` unchanged. It was the largest
single block in that function and none of it is about building the price frames
the rest of the module assembles — it reads a different table, on a different
calendar, with its own failure modes.

The point-in-time semantics live in `data.py` (`_query_fundamentals_pit`,
`_pit_frame`): rows are keyed on `filing_date`, a restatement only becomes
visible on its own filing date and only if it is of the newest period known by
then (`latest_period_rows`), and a value whose source filing has gone stale is
blanked rather than forward-filled forever. What this module adds is the attachment — putting
those frames into the shared dict under the `f_` names strategies read.
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
    load_stock_actions,
    split_multiplier_frame,
)

logger = logging.getLogger(__name__)


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
            "f_net_income_ttm_m",
        ]
        for key in _all_monthly_keys:
            shared[key] = pd.DataFrame(index=rebal_dates, dtype=float)

    if market == "B3":
        _split_adjust_shares(shared, db_path, start, end, ret)

    # ── Dynamic ratio computation from raw monthly inputs + raw_close ────────
    # Ratios are computed here rather than loaded from stored columns.
    # raw_close is already in shared (period-end resampled, unadjusted close);
    # the share counts were put on its basis above.
    #
    # Earnings are trailing twelve months. CVM stores ITR income year-to-date
    # (Q1 = 3 months, Q2 = 6, Q3 = 9), so f_net_income_m / f_ebitda_m made P/E
    # ~4x too high after Q1 filings and ~2x after Q2, and the reporting calendar
    # rather than the price decided who passed LowPE's 1..30 band. P/E reads
    # the materialized net_income_ttm; EBITDA has no stored TTM, so it uses
    # f_ebitda_ttm (data.ttm_from_ytd) on the same cells as f_ebitda_m.
    _shares_m = shared.get("f_shares_outstanding_m", pd.DataFrame())
    _ni_m = shared.get("f_net_income_ttm_m", pd.DataFrame())
    _equity_m = shared.get("f_equity_m", pd.DataFrame())
    _ebitda_m = shared.get("f_ebitda_m", pd.DataFrame())
    _ebitda_ttm = shared.get("f_ebitda_ttm", pd.DataFrame())
    if not _ebitda_m.empty:
        _ebitda_m = (_ebitda_ttm.reindex(index=_ebitda_m.index, columns=_ebitda_m.columns)
                     if not _ebitda_ttm.empty else pd.DataFrame())
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


def _split_adjust_shares(shared: dict, db_path: str, start: str, end: str,
                         ret: pd.DataFrame) -> None:
    """Put f_shares_outstanding(_m) on the basis of the rebalance-date close.

    Share counts come from filings up to 400 days old (the FRE is annual);
    raw_close is the unadjusted close of the rebalance date. After a 1:10
    desdobramento in between, close x shares was a tenth of the company — P/E
    and P/B ten times "cheaper", exactly the names a value screen buys, and
    Teva's market-cap floor misapplied. Each count is multiplied by the stock
    actions with ex_date in (period_end of its filing, rebalance date], the
    correction sp500_b3.share_multiplier applies per name.

    The anchor is the period_end of the fundamentals_pit row behind each cell,
    rebuilt here with the same as-of rules. Where none is found (or the
    actions cannot be read) the count is left as it was.
    """
    try:
        long = _query_fundamentals_pit(db_path, ["shares_outstanding"], end, "B3")
        actions = load_stock_actions(db_path)
    except Exception as e:
        logger.warning(f"Share counts not split-adjusted (could not read inputs): {e}")
        return
    if long.empty or actions.empty:
        return
    anchor = _pit_frame(long, "shares_outstanding", ret.index, start, 400, value="period_end")
    anchor = _broadcast_roots_to_tickers(anchor, ret.columns)
    mult = split_multiplier_frame(actions, anchor)
    for key in ("f_shares_outstanding_m", "f_shares_outstanding"):
        shares = shared.get(key)
        if shares is None or shares.empty:
            continue
        shared[key] = shares * mult.reindex(index=shares.index, columns=shares.columns).fillna(1.0)
