"""
Mean-Reversion Composite Strategy Helpers
==========================================
Helper functions for the 4-layer mean-reversion composite alpha strategy.
Separated from the strategy plugin so build_shared_data() can precompute the
features once for every strategy that wants them.

compute_mean_rev_features()        -- shared feature computation (used by both data paths)
                                      built from the one-feature functions below,
                                      which shared_data defers individually
Layer 1: compute_regime_filter()   -- CDI/IBOV macro regime gating
Layer 2: compute_alpha_score()     -- three sub-signal composite alpha
Layer 4: compute_signal_stability() -- rolling IC signal stability guard
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# CDI NaN guard: match base_signals.py formula max(20, window * 0.6) where window=63
_CDI_MIN_OBS = max(20, int(np.ceil(63 * 0.6)))  # = 38


def autocorr(adj_close: pd.DataFrame, window: int, min_periods: int) -> pd.DataFrame:
    """Lag-1 autocorrelation of daily returns, vectorised as rolling cov / var.

    Split out of ``compute_mean_rev_features`` so ``build_shared_data`` can defer
    each feature on its own. Every one of these is a full (date x ticker) daily
    frame, and on the US plane that is hundreds of MB apiece — a momentum book
    that never reads them should never pay for them. Recomputing ``pct_change``
    per feature costs one transient frame; holding all six costs six.
    """
    daily_ret = adj_close.pct_change(1)
    cov = daily_ret.rolling(window, min_periods=min_periods).cov(daily_ret.shift(1))
    var = daily_ret.rolling(window, min_periods=min_periods).var()
    return (cov / var.replace(0, np.nan)).shift(1)


def high_low_range(split_adj_high: pd.DataFrame, split_adj_low: pd.DataFrame,
                   split_adj_close: pd.DataFrame, window: int = 20,
                   min_periods: int = 10) -> pd.DataFrame:
    """Mean daily high-low range over `window`, normalised by close."""
    hlr_daily = (split_adj_high - split_adj_low) / split_adj_close.replace(0, np.nan)
    return hlr_daily.rolling(window, min_periods=min_periods).mean().shift(1)


def rolling_vol_daily(adj_close: pd.DataFrame, window: int,
                      min_periods: int) -> pd.DataFrame:
    """Rolling std of daily returns, lagged one day."""
    return (adj_close.pct_change(1)
            .rolling(window, min_periods=min_periods).std().shift(1))


def cdi_cumul(cdi_daily: pd.Series, window: int = 63) -> pd.Series:
    """Cumulative CDI over `window` trading days, NaN until enough observations."""
    cdi_cumprod = (1 + cdi_daily).cumprod()
    out = cdi_cumprod / cdi_cumprod.shift(window) - 1
    out[cdi_daily.expanding().count() < _CDI_MIN_OBS] = np.nan
    return out


def compute_mean_rev_features(
    adj_close: pd.DataFrame,
    split_adj_high: pd.DataFrame,
    split_adj_low: pd.DataFrame,
    split_adj_close: pd.DataFrame,
    cdi_daily: pd.Series,
    ibov_daily_ret: pd.Series,
    ibov_px: pd.Series,
) -> dict:
    """
    Compute mean-reversion composite features at daily frequency.

    Shared by both build_shared_data() and build_strategy_returns() to ensure
    identical feature computation across both code paths. Every entry is one of
    the functions above, so there is still exactly one definition of each
    formula whether it is reached eagerly through here or deferred key by key.

    Returns dict with keys:
        autocorr_20d, autocorr_60d, high_low_range_20d,
        rolling_vol_20d_daily, rolling_vol_60d_daily,
        cdi_cumul_63d, ibov_vol_20d_daily, ibov_ret_20d_daily
    """
    return {
        "autocorr_20d": autocorr(adj_close, 20, 10),
        "autocorr_60d": autocorr(adj_close, 60, 30),
        "high_low_range_20d": high_low_range(
            split_adj_high, split_adj_low, split_adj_close),
        "rolling_vol_20d_daily": rolling_vol_daily(adj_close, 20, 10),
        "rolling_vol_60d_daily": rolling_vol_daily(adj_close, 60, 30),
        "cdi_cumul_63d": cdi_cumul(cdi_daily),
        "ibov_vol_20d_daily": ibov_daily_ret.rolling(20).std().shift(1),
        "ibov_ret_20d_daily": ibov_px.pct_change(20),
    }


def compute_regime_filter(
    shared_data: dict,
    params: dict,
    freq: str = "ME",
) -> pd.Series:
    """
    Layer 1: Macro regime filter.

    Returns a boolean Series indexed by month-end dates.
    True = Risk-On (deploy capital), False = Risk-Off (reduce/exit).

    Gates:
    1. CDI tightening: cdi_cumul_63d rising month-over-month
    2. IBOV vol stress: ibov_vol_pctrank > 0.70
    3. IBOV drawdown (optional): ibov_ret_20d < threshold
    """
    cdi_cumul_63d = shared_data["cdi_cumul_63d"]
    ibov_vol_pctrank = shared_data["ibov_vol_pctrank"]
    ibov_ret_20d_daily = shared_data["ibov_ret_20d_daily"]

    ibov_drawdown_gate = params.get("ibov_drawdown_gate", "No") == "Yes"
    ibov_drawdown_threshold = params.get("ibov_drawdown_threshold", -0.10)

    # Gate 1: CDI tightening (current month > prior month)
    cdi_m = cdi_cumul_63d.resample(freq).last()
    cdi_tightening = cdi_m > cdi_m.shift(1)

    # Gate 2: IBOV vol stress (lagged 1 month to avoid lookahead)
    # ibov_vol_pctrank is already monthly from shared_data
    ibov_stressed = ibov_vol_pctrank.shift(1) > 0.70

    # Gate 3: IBOV drawdown (optional)
    ibov_ret_m = ibov_ret_20d_daily.resample(freq).last().shift(1)
    ibov_deep_dd = ibov_ret_m < ibov_drawdown_threshold

    # Risk-On = none of the enabled gates trigger
    risk_off = cdi_tightening | ibov_stressed
    if ibov_drawdown_gate:
        risk_off = risk_off | ibov_deep_dd

    risk_on = ~risk_off
    # Fill NaN as Risk-Off (conservative during warmup)
    risk_on = risk_on.fillna(False)

    return risk_on


def _alpha_inputs(shared_data: dict, freq: str, grid: pd.DataFrame) -> dict:
    """The per-ticker half of the alpha score, already on the rebalance grid.

    Every sub-signal below is a chain of per-ticker operations — a rolling
    window, a ratio to the name's own mean, a resample — followed by a
    cross-sectional rank. Only the rank is cross-sectional, and it happens in
    `compute_alpha_score` on what this returns. So everything here is computed
    inside the batch its bars arrive in and never exists at the market's width.

    That is the whole memory story for this strategy: it read five daily panels
    at once (~580 MB each on the US plane) plus a rolling window over each, and
    peaked past 11 GB. The frames below are ~26 MB apiece.
    """
    from backtests.core.strategy_base import daily_on_grid

    def ratio_to_own_mean(daily: pd.DataFrame) -> pd.DataFrame:
        """A name against its own 60-day norm — elevated vol reads as trending."""
        mean_60d = daily.rolling(60, min_periods=30).mean()
        return daily / mean_60d.replace(0, np.nan)

    def atr(daily: pd.DataFrame) -> pd.DataFrame:
        return daily.pct_change().abs().ewm(span=14, min_periods=14).mean()

    out = daily_on_grid(
        shared_data, "adj_close", grid,
        return_60d_m=lambda a: a.pct_change(60).resample(freq).last(),
        ac20_m=lambda a: autocorr(a, 20, 10).resample(freq).last(),
        ac60_m=lambda a: autocorr(a, 60, 30).resample(freq).last(),
        vol_ratio_m=lambda a: (
            rolling_vol_daily(a, 60, 30)
            / rolling_vol_daily(a, 20, 10).replace(0, np.nan)).resample(freq).last(),
        atr_ratio_m=lambda a: ratio_to_own_mean(atr(a)).resample(freq).last(),
    )
    sides = ("split_adj_high", "split_adj_low", "split_adj_close")
    out.update(daily_on_grid(
        shared_data, sides, grid,
        hlr_m=lambda b: high_low_range(*(b[k] for k in sides)).resample(freq).last(),
        hl_vol_ratio_m=lambda b: ratio_to_own_mean(
            high_low_range(*(b[k] for k in sides))).resample(freq).last(),
    ))
    return out


def compute_alpha_score(
    shared_data: dict,
    params: dict,
    freq: str = "ME",
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """
    Layer 2: Composite alpha score from sub-signals A–D (and optional E).

    Returns:
        alpha_score: DataFrame (month-end x ticker) with composite score [0,1] range
        sub_signals: dict with keys sub_A..sub_D for IC monitoring,
                     plus optional "atr_ratio_m" (raw ratio, for the ATR gate).
    """
    cdi_cumul_63d = shared_data["cdi_cumul_63d"]
    grid = shared_data["ret"]
    on_grid = _alpha_inputs(shared_data, freq, grid)

    w_vol = params.get("w_vol", 0.25)
    w_macro = params.get("w_macro", 0.25)
    w_autocorr = params.get("w_autocorr", 0.25)
    w_vol_ratio = params.get("w_vol_ratio", 0.25)

    # ── Sub-signal A: Volatility Reversal ──────────────────────────────────
    rank_hlr = (-on_grid["hlr_m"]).rank(axis=1, pct=True)
    rank_vol_ratio = (-on_grid["vol_ratio_m"]).rank(axis=1, pct=True)
    sub_A = (rank_hlr + rank_vol_ratio) / 2.0

    # ── Sub-signal B: Macro-Relative Momentum ─────────────────────────────
    cdi_63d_m = cdi_cumul_63d.resample(freq).last()
    # Avoid division by zero; broadcast Series across columns
    cdi_63d_safe = cdi_63d_m.replace(0, np.nan)
    macro_ratio = on_grid["return_60d_m"].div(cdi_63d_safe, axis=0)
    sub_B = macro_ratio.rank(axis=1, pct=True)

    # ── Sub-signal C: Autocorrelation Regime ───────────────────────────────
    rank_ac60 = (-on_grid["ac60_m"]).rank(axis=1, pct=True)
    rank_ac20 = (-on_grid["ac20_m"]).rank(axis=1, pct=True)
    sub_C = (rank_ac60 + rank_ac20) / 2.0

    # ── Sub-signal D: High-Low Range ratio to own 60d mean ─────────────────
    # Stocks whose current vol is elevated above their own norm get a LOW score
    # (likely trending, not mean-reverting). Negative IC → flip sign before ranking.
    sub_D = (-on_grid["hl_vol_ratio_m"]).rank(axis=1, pct=True)

    # ── Composite ──────────────────────────────────────────────────────────
    alpha = w_vol * sub_A + w_macro * sub_B + w_autocorr * sub_C + w_vol_ratio * sub_D

    sub_signals: dict[str, pd.DataFrame] = {
        "sub_A": sub_A,
        "sub_B": sub_B,
        "sub_C": sub_C,
        "sub_D": sub_D,
        # Not added to composite alpha; the raw ratio for the ATR gate in
        # generate_signals, not for the IC guard.
        "atr_ratio_m": on_grid["atr_ratio_m"],
    }
    return alpha, sub_signals


def _compute_trailing_ic(
    signal_monthly: pd.DataFrame,
    fwd_ret_monthly: pd.DataFrame,
    start_idx: int,
    end_idx: int,
) -> float:
    """
    Compute mean cross-sectional IC (Spearman rank correlation) over a trailing window.
    signal_monthly and fwd_ret_monthly are (months x tickers).
    """
    ics = []
    for j in range(start_idx, end_idx):
        if j >= len(signal_monthly) or j >= len(fwd_ret_monthly):
            break
        sig_row = signal_monthly.iloc[j]
        fwd_row = fwd_ret_monthly.iloc[j]
        # Only stocks with both values
        both_valid = sig_row.notna() & fwd_row.notna()
        if both_valid.sum() < 10:
            continue
        sig_ranks = sig_row[both_valid].rank(pct=True)
        fwd_ranks = fwd_row[both_valid].rank(pct=True)
        ic = sig_ranks.corr(fwd_ranks)
        if not np.isnan(ic):
            ics.append(ic)
    return np.mean(ics) if ics else np.nan


def compute_signal_stability(
    sub_signals: dict[str, pd.DataFrame],
    forward_returns_monthly: pd.DataFrame,
    base_weights: dict[str, float],
    params: dict,
) -> pd.DataFrame:
    """
    Layer 4: Signal stability guard.

    Monitors rolling IC and adaptively disables degraded sub-signals.

    Args:
        sub_signals: {"sub_A": monthly DataFrame, "sub_B": ..., "sub_C": ...}
        forward_returns_monthly: DataFrame of realized next-month returns (ret.shift(-1))
        base_weights: {"sub_A": 0.333, "sub_B": 0.333, "sub_C": 0.333}
        params: strategy parameters

    Returns:
        DataFrame indexed by month-end dates, columns ["w_sub_A", "w_sub_B", "w_sub_C"]
    """
    enable = params.get("enable_stability_guard", "Yes") == "Yes"
    ic_check_freq = params.get("ic_check_freq", 3)
    trailing_months = params.get("ic_trailing_months", 12)
    flip_threshold = params.get("ic_flip_consecutive", 2)

    # Derive signal_names from base_weights keys that are actual sub-signals in sub_signals.
    # This makes the guard automatically handle any new sub-signals (sub_D, sub_E, etc.)
    signal_names = [s for s in base_weights if s in sub_signals]
    expected_sign = {s: 1.0 for s in signal_names}  # all signals are positive-IC after sign flip

    if not signal_names:
        # No valid signals — return empty DataFrame
        return pd.DataFrame()

    dates = sub_signals[signal_names[0]].index
    n_months = len(dates)

    # Initialize output with base weights
    weight_records = []

    if not enable:
        for i in range(n_months):
            row = {"date": dates[i]}
            for s in signal_names:
                row[f"w_{s}"] = base_weights.get(s, 0.333)
            weight_records.append(row)
        return pd.DataFrame(weight_records).set_index("date")

    # Track consecutive flipped IC counts
    flip_counts = {s: 0 for s in signal_names}
    active = {s: True for s in signal_names}
    last_check_month = -999  # force first check

    for i in range(n_months):
        # Check if it's time for an IC check
        months_since_check = i - last_check_month
        warmup_ok = i >= trailing_months + 1  # need trailing window + 1 for forward returns

        if warmup_ok and months_since_check >= ic_check_freq:
            last_check_month = i
            # Compute trailing IC for each sub-signal
            # Forward returns: at month i, we know returns up to month i-1
            # So trailing window is [i - trailing_months - 1, i - 1)
            start = max(0, i - trailing_months - 1)
            end = i - 1  # exclusive of month i (avoid lookahead)

            for s in signal_names:
                ic = _compute_trailing_ic(
                    sub_signals[s], forward_returns_monthly, start, end
                )
                if np.isnan(ic):
                    continue  # insufficient data, keep current state

                # Check if IC has flipped from expected sign
                if ic * expected_sign[s] < 0:
                    flip_counts[s] += 1
                else:
                    flip_counts[s] = 0
                    active[s] = True  # re-enable if IC reverts

                if flip_counts[s] >= flip_threshold:
                    active[s] = False

        # Compute effective weights for this month
        active_signals = [s for s in signal_names if active[s]]
        row = {"date": dates[i]}

        if not active_signals:
            # All signals disabled -- fall back to base weights (safety)
            for s in signal_names:
                row[f"w_{s}"] = base_weights.get(s, 0.333)
        else:
            # Equal redistribution: disabled weight split equally among active signals
            disabled_weight = sum(
                base_weights.get(s, 0.333) for s in signal_names if not active[s]
            )
            n_active = len(active_signals)
            redistribution = disabled_weight / n_active
            for s in signal_names:
                if active[s]:
                    row[f"w_{s}"] = base_weights.get(s, 0.333) + redistribution
                else:
                    row[f"w_{s}"] = 0.0

        weight_records.append(row)

    return pd.DataFrame(weight_records).set_index("date")
