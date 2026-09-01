"""Point-in-time fundamentals, and the ratios computed from them.

Lifted out of `shared_data.build_shared_data` unchanged. It was the largest
single block in that function and none of it is about building the price frames
the rest of the module assembles — it reads a different table, on a different
calendar, with its own failure modes.

The point-in-time semantics live in `data.py` (`_query_fundamentals_pit`,
`_pit_frame`): rows are keyed on `filing_date`, restatements are never
deduplicated, and a value whose source filing has gone stale is blanked rather
than forward-filled forever. What this module adds is the attachment — putting
those frames into the shared dict under the `f_` names strategies read.
"""

import logging

import pandas as pd

from backtests.core.data import (
    compute_ev_ebitda_dynamic,
    compute_pb_ratio_dynamic,
    compute_pe_ratio_dynamic,
    load_all_fundamentals,
    load_all_fundamentals_monthly,
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
