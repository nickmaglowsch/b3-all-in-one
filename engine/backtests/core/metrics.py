"""
Core metrics and evaluation utilities for backtesting.
"""

import numpy as np
import pandas as pd


def cumret(ret: pd.Series) -> pd.Series:
    return (1 + ret).cumprod()


def value_to_ret(values: pd.Series, contributions: pd.Series | None = None) -> pd.Series:
    """Convert BRL equity curve to simple period returns.

    With periodic buy-ins (``run_simulation(contribution=...)``) a raw
    ``pct_change`` reads each deposit as a gain, inflating Return/Vol/Sharpe.
    Pass the sim's ``contributions`` Series to strip them out and get the
    time-weighted return: the cash lands *after* the period's returns are
    applied, so ``V_t - C_t`` is the pre-deposit mark.
    """
    if contributions is None:
        return values.pct_change().fillna(0)
    c = contributions.reindex(values.index).fillna(0.0)
    ret = (values - c) / values.shift(1) - 1
    return ret.replace([np.inf, -np.inf], 0.0).fillna(0)   # inf: zero-capital start


def ann_return(ret: pd.Series, periods_per_year: int = 12) -> float:
    n = len(ret) / periods_per_year
    return (1 + ret).prod() ** (1 / n) - 1 if n > 0 else 0.0


def ann_vol(ret: pd.Series, periods_per_year: int = 12) -> float:
    return ret.std() * np.sqrt(periods_per_year)


def sharpe(ret: pd.Series, risk_free: float = 0.0, periods_per_year: int = 12) -> float:
    # `!= 0` only caught a std of *exactly* zero. A constant non-zero series —
    # a flat CDI sleeve, a park row repeated — lands on ~5e-19 instead, and the
    # division printed a Sharpe of 1.9e16 that sorts first in any gallery.
    sd = ret.std()
    mean_ret = ret.mean() - (risk_free / periods_per_year)
    return (mean_ret / sd) * np.sqrt(periods_per_year) if sd > 1e-12 else 0.0


def max_dd(ret: pd.Series) -> float:
    cum = cumret(ret)
    return (cum / cum.cummax() - 1).min()


def reconstruct_daily_values(
    target_weights: pd.DataFrame,
    daily_ret: pd.DataFrame,
    initial_capital: float = 100_000.0,
) -> pd.Series:
    """Daily mark-to-market NAV implied by rebalance-date target weights.

    run_simulation records NAV only on rebalance dates, so max_dd / ann_vol are
    blind to intra-period lows — a quarterly curve never sees a mid-quarter
    crash, and even a monthly/weekly curve misses the trough between marks. This
    reconstructs the buy-and-hold daily path: on each rebalance date the
    portfolio is reset to that row's target weights, then drifts with daily
    asset returns until the next rebalance. Tax and slippage are ignored — they
    bite only on rebalance dates and are second-order for a risk-path estimate.

    ``daily_ret`` must carry a column for every ticker with a non-zero weight
    (missing columns are treated as flat / zero return, e.g. an uninvested
    cash residual).
    """
    cols = [c for c in target_weights.columns if c in daily_ret.columns]
    if not cols:
        return pd.Series(dtype=float)
    # Guard against silent wrongness: if any held sleeve has no daily series
    # (e.g. a downloaded ETF the caller didn't supply), bail so the caller falls
    # back to cadence DD rather than a path that zeros that sleeve.
    missing = [c for c in target_weights.columns if c not in daily_ret.columns]
    if missing and float(target_weights[missing].abs().sum(axis=1).max()) > 1e-4:
        return pd.Series(dtype=float)
    tw = target_weights[cols].fillna(0.0)
    live = tw.index[tw.abs().sum(axis=1) > 0]
    if len(live) == 0:
        return pd.Series(dtype=float)
    tw = tw.loc[live[0]:]
    reb = tw.index
    dr = daily_ret[cols]

    # Scale-free: this path only feeds ratio metrics (drawdown, Calmar), and a
    # pure-DCA run starts at 0 — which would flatten it to zeros and print a
    # fake 0% max DD. Any positive base gives the same ratios.
    nav = float(initial_capital) or 1.0
    segments = [pd.Series([nav], index=[reb[0]])]
    for i, d0 in enumerate(reb):
        d1 = reb[i + 1] if i + 1 < len(reb) else dr.index[-1]
        # Trading days strictly after this rebalance, up to and including the
        # next one — a clean partition even when d0/d1 are calendar month-ends
        # (resample labels) that aren't themselves trading days.
        seg = dr.loc[(dr.index > d0) & (dr.index <= d1)]
        if seg.empty:
            continue
        w = tw.loc[d0].to_numpy()
        resid = 1.0 - float(w.sum())     # uninvested fraction: hold flat as cash,
                                         # don't vaporize it (else NAV fake-drops
                                         # to Σw at every not-fully-invested rebalance)
        # Clip daily returns to the simulator's own guard band (_apply_returns)
        # so an unrecorded-split print can't crater the reconstructed path.
        growth = (1.0 + seg.fillna(0.0).clip(-0.90, 5.0)).cumprod()   # per-asset cum growth
        seg_nav = pd.Series(((growth.to_numpy() * w).sum(axis=1) + resid) * nav, index=seg.index)
        segments.append(seg_nav)
        nav = float(seg_nav.iloc[-1])

    out = pd.concat(segments)
    return out[~out.index.duplicated(keep="first")]


def _held_daily_returns(shared: dict, target_weights: pd.DataFrame):
    """Daily returns of the names in `target_weights`.

    Through the bundle's scoped reader when it has one, and off a resident frame
    otherwise — a hand-built ``shared`` dict in a test, or a caller that already
    holds the panel. ``None`` when neither is available, which is the "fall back
    to cadence drawdown" signal the caller already handles.
    """
    reader = shared.get("daily_returns")
    if not callable(reader):
        resident = shared.get("split_adj_close")
        return None if resident is None else resident.pct_change()
    # The names the book actually held, not the names it could have held. A
    # target_weights frame carries a column per candidate and a zero in almost
    # all of them — on the US plane, eleven thousand columns to describe a
    # thirty-name book. `reconstruct_daily_values` ignores a column that is
    # always zero (and its own guard only refuses a *missing* column that
    # carries weight), so reading the daily close for the rest is pure cost:
    # it is what took a US run from 1.1 GB to 5.8 GB after the load was fixed.
    held = [c for c in target_weights.columns
            if c not in ("CDI_ASSET", "IBOV")
            and float(target_weights[c].abs().max() or 0.0) > 0.0]
    return reader("split_adj_close", columns=held)





def strategy_daily_values(
    shared: dict,
    target_weights: pd.DataFrame,
    initial_capital: float = 100_000.0,
) -> pd.Series:
    """Daily NAV path for a strategy's rebalance-date weights, built from the
    daily price data already in ``shared``.

    Single source of truth so Max Drawdown / Calmar reflect intra-rebalance lows
    for EVERY strategy and rebalance regime — the rebalance-cadence equity curve
    from run_simulation is blind to mid-period crashes (see
    :func:`reconstruct_daily_values`). Returns an empty Series if the required
    daily data isn't present, in which case callers fall back to cadence DD.

    What it needs is the daily path of the names the book actually held — a
    dozen or two — so when the bundle can scope a panel by ticker it is asked to
    (`shared["panel"]`, see shared_data). Reading the market's whole daily close
    to use twenty columns of it is what a US backtest could not afford.
    """
    daily_ret = _held_daily_returns(shared, target_weights)
    if daily_ret is None:
        return pd.Series(dtype=float)

    # The sleeves that are not columns of the price matrix: the cash leg, the
    # benchmark, and any downloaded ETF the FixedWeight blend engine stashed on
    # the bundle. Gathered and joined once — assigning them in one at a time
    # reconsolidates the whole frame per column, which on a wide book is a full
    # copy of it each time.
    extra = {}
    cdi_daily = shared.get("cdi_daily")
    if cdi_daily is not None:
        extra["CDI_ASSET"] = cdi_daily
    ibov_px = shared.get("ibov_px")
    if "IBOV" in target_weights.columns and ibov_px is not None:
        extra["IBOV"] = ibov_px.pct_change()
    extra.update(shared.get("_daily_asset_ret") or {})
    if extra:
        daily_ret = pd.concat(
            [daily_ret, pd.DataFrame(extra).reindex(daily_ret.index)], axis=1)
    return reconstruct_daily_values(target_weights, daily_ret, initial_capital)


def build_metrics(
    ret: pd.Series,
    label: str,
    periods_per_year: int = 12,
    daily_values: pd.Series | None = None,
) -> dict:
    """Standardized performance dictionary.

    When ``daily_values`` (a daily NAV path, e.g. from
    :func:`reconstruct_daily_values`) is supplied, Max Drawdown and Calmar are
    computed on it so they reflect intra-rebalance lows; otherwise they fall
    back to the rebalance-cadence ``ret`` (which understates drawdown). Return,
    volatility and Sharpe always use ``ret`` (annualized-from-periodic is the
    standard convention).
    """
    if daily_values is not None and daily_values.dropna().shape[0] > 1:
        mdd = max_dd(value_to_ret(daily_values.dropna()))
    else:
        mdd = max_dd(ret)
    ar = ann_return(ret, periods_per_year)
    cal = ar / abs(mdd) if mdd != 0 else 0.0
    return {
        "Strategy": label,
        "Ann. Return (%)": round(ar * 100, 2),
        "Ann. Volatility (%)": round(ann_vol(ret, periods_per_year) * 100, 2),
        "Sharpe": round(sharpe(ret, periods_per_year=periods_per_year), 2),
        "Max Drawdown (%)": round(mdd * 100, 2),
        "Calmar": round(cal, 2),
    }


def display_metrics_table(metrics_list: list):
    """Print performance metrics to console"""
    n_cols = len(metrics_list)
    dash_length = 30 + 16 * n_cols

    print("\n" + "-" * dash_length)

    headers = ["Metric"] + [m["Strategy"] for m in metrics_list]
    header_fmt = "  {0:<26}  " + "  ".join(
        [f"{{{i}:>14}}" for i in range(1, n_cols + 1)]
    )
    print(header_fmt.format(*headers))
    print("-" * dash_length)

    col_labels = list(metrics_list[0].keys())
    for key in col_labels:
        if key == "Strategy":
            continue
        vals = [key] + [str(m[key]) for m in metrics_list]
        print(header_fmt.format(*vals))
    print("-" * dash_length + "\n")
