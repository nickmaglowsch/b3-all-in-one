"""
Core metrics and evaluation utilities for backtesting.
"""

import itertools

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
    return _reconstruct(target_weights, daily_ret.index, daily_ret.columns,
                        [daily_ret], initial_capital)


def _investable_weights(target_weights: pd.DataFrame, available, days):
    """`target_weights` trimmed to what can actually be priced, or ``None``.

    ``None`` is the "fall back to cadence drawdown" signal the caller handles: no
    priceable column, no daily calendar, no row with any weight on it, or — the
    one worth guarding — a held sleeve with no daily series at all, e.g. a
    downloaded ETF the caller did not supply. Zeroing that sleeve would print a
    path that is quietly wrong rather than absent.
    """
    have = set(available)
    cols = [c for c in target_weights.columns if c in have]
    missing = [c for c in target_weights.columns if c not in have]
    if not cols or len(days) == 0:
        return None
    if missing and float(target_weights[missing].abs().sum(axis=1).max()) > 1e-4:
        return None
    tw = target_weights[cols].fillna(0.0)
    live = tw.index[tw.abs().sum(axis=1) > 0]
    return None if len(live) == 0 else tw.loc[live[0]:]


def _segments(reb: pd.Index, days: pd.DatetimeIndex) -> list:
    """``(rebalance date, positions in `days`)`` per holding period.

    Trading days strictly after each rebalance up to and including the next — a
    clean partition even when the marks are calendar month-ends (resample
    labels) that are not themselves trading days.
    """
    spans = []
    for i, d0 in enumerate(reb):
        d1 = reb[i + 1] if i + 1 < len(reb) else days[-1]
        pos = np.flatnonzero((days > d0) & (days <= d1))
        if len(pos):
            spans.append((d0, pos))
    return spans


def _reconstruct(target_weights: pd.DataFrame, days, available, chunks,
                 initial_capital: float) -> pd.Series:
    """The path above, over column chunks that arrive one at a time.

    Within a rebalance segment the NAV is `nav * (Σ_i w_i · growth_i(t) + resid)`
    — a *sum over names* of each name's own cumulative growth. So it decomposes:
    a chunk of names contributes its own terms to that sum and nothing else, and
    the segment's factor path can be accumulated a chunk at a time into one array
    of `len(days)` floats. NAV is then chained across segments at the end, which
    needs no price data at all.

    That is what lets the daily path be reconstructed for a book of ten thousand
    names without ten thousand columns of daily returns in memory — it was ~950 MB
    of the peak of every US run, for a curve that only feeds drawdown and Calmar.

    `chunks` must cover the held names once each; overlapping chunks would
    double-count. `available` is every column they will supply between them,
    needed up front for the missing-sleeve guard below.
    """
    days = pd.DatetimeIndex(days)
    tw = _investable_weights(target_weights, available, days)
    if tw is None:
        return pd.Series(dtype=float)
    cols, reb = list(tw.columns), tw.index
    spans = _segments(reb, days)

    factor = np.zeros(len(days))
    covered: set = set()
    for chunk in chunks:
        part = [c for c in cols if c in chunk.columns]
        if not part:
            continue
        covered.update(part)
        weights, returns = tw[part], chunk[part].to_numpy(dtype=float)
        for d0, pos in spans:
            # Clip daily returns to the simulator's own guard band (_apply_returns)
            # so an unrecorded-split print can't crater the reconstructed path.
            seg = np.where(np.isnan(returns[pos]), 0.0, returns[pos])
            growth = np.cumprod(1.0 + np.clip(seg, -0.90, 5.0), axis=0)
            factor[pos] += growth @ weights.loc[d0].to_numpy()
        del weights, returns

    # A column the caller said it had but supplied no data for holds flat, which
    # is what an all-NaN column does in the gathered form: zero returns, growth
    # of one, its weight carried unchanged. Absent from the sum it would read as
    # weight zero instead — a silent haircut on every segment that held it.
    flat = [c for c in cols if c not in covered]
    if flat:
        for d0, pos in spans:
            factor[pos] += float(tw.loc[d0, flat].sum())

    # Scale-free: this path only feeds ratio metrics (drawdown, Calmar), and a
    # pure-DCA run starts at 0 — which would flatten it to zeros and print a
    # fake 0% max DD. Any positive base gives the same ratios.
    nav = float(initial_capital) or 1.0
    segments = [pd.Series([nav], index=[reb[0]])]
    for d0, pos in spans:
        # The uninvested fraction: held flat as cash, not vaporized (else NAV
        # fake-drops to Σw at every not-fully-invested rebalance).
        resid = 1.0 - float(tw.loc[d0].to_numpy().sum())
        seg_nav = (factor[pos] + resid) * nav
        segments.append(pd.Series(seg_nav, index=days[pos]))
        nav = float(seg_nav[-1])

    out = pd.concat(segments)
    return out[~out.index.duplicated(keep="first")]


def _extra_sleeves(shared: dict, target_weights: pd.DataFrame) -> dict:
    """The daily return series that are not columns of the price matrix: the cash
    leg, the benchmark, and any downloaded ETF the FixedWeight blend engine
    stashed on the bundle."""
    extra = {}
    cdi_daily = shared.get("cdi_daily")
    if cdi_daily is not None:
        extra["CDI_ASSET"] = cdi_daily
    ibov_px = shared.get("ibov_px")
    if "IBOV" in target_weights.columns and ibov_px is not None:
        extra["IBOV"] = ibov_px.pct_change()
    extra.update(shared.get("_daily_asset_ret") or {})
    return extra


def strategy_daily_values(
    shared: dict,
    target_weights: pd.DataFrame,
    initial_capital: float = 100_000.0,
) -> pd.Series:
    """Daily NAV path for a strategy's rebalance-date weights.

    Single source of truth so Max Drawdown / Calmar reflect intra-rebalance lows
    for EVERY strategy and rebalance regime — the rebalance-cadence equity curve
    from run_simulation is blind to mid-period crashes (see
    :func:`reconstruct_daily_values`). Returns an empty Series if the required
    daily data isn't present, in which case callers fall back to cadence DD.

    Streamed when the bundle can stream. A book that rotates monthly over a big
    universe has held nearly every name in it by the end — 9,694 of 11,000 on a
    26-year US run — so "only the names it held" is not the saving it sounds
    like. Feeding those names to `_reconstruct` a batch at a time is: the path is
    a sum over names, so it never needs more than one batch of daily returns and
    one array of `len(sessions)` floats.
    """
    extra = _extra_sleeves(shared, target_weights)
    held = [c for c in target_weights.columns
            if c not in extra
            and float(target_weights[c].abs().max() or 0.0) > 0.0]

    stream, sessions = shared.get("stream"), shared.get("sessions")
    if not callable(stream) or sessions is None:
        # A hand-built bundle (tests) or a caller that already holds the panel.
        daily_close = shared.get("split_adj_close")
        if daily_close is None:
            return pd.Series(dtype=float)
        daily_ret = daily_close.pct_change()
        if extra:
            daily_ret = pd.concat(
                [daily_ret, pd.DataFrame(extra).reindex(daily_ret.index)], axis=1)
        return reconstruct_daily_values(target_weights, daily_ret, initial_capital)

    days = pd.DatetimeIndex(sessions)
    sleeves = pd.DataFrame(extra).reindex(days) if extra else pd.DataFrame(index=days)
    chunks = itertools.chain(
        [sleeves],
        stream("split_adj_close", columns=held, reduce=lambda f: f.pct_change()))
    return _reconstruct(target_weights, days, [*held, *extra], chunks,
                        initial_capital)


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

