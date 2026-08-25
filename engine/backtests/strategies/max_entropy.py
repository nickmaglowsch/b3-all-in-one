"""MaxEntropy — maximum-entropy (Gibbs/Boltzmann) allocation.

The allocation rule is the solution to a variational problem, not an analogy.
Ask for the *most diversified* book — the weight vector of maximum Shannon
entropy ``S(w) = -Σ wᵢ ln wᵢ`` — subject to a risk budget ``Σ wᵢ Eᵢ = Ē``,
where ``Eᵢ`` is the asset's realised variance. Lagrange on those two constraints
(weights sum to 1, energy averages to Ē) and the solution is the Gibbs measure:

    wᵢ = exp(-Eᵢ / T) / Z,       Z = Σⱼ exp(-Eⱼ / T)

``T`` is the multiplier on the risk budget — the temperature. That is the whole
strategy. What makes it worth having is that ``T`` is a genuine dial between the
two allocations everyone already uses, with everything in between available:

    T → ∞   every exponent → 0        → equal weight (max entropy, no budget)
    T → 0   the coldest name dominates → all-in on the lowest-variance name
    T ~ 1   a smooth tilt toward calm names, entropy traded for risk

Equal weight and minimum-variance concentration are the endpoints of one family,
and ``T`` says where on it you want to sit. Note this is *not* inverse-vol
(``w ∝ 1/σ``): the ensemble decays exponentially in variance, not inversely in
volatility, so it separates calm from turbulent names more sharply — and by an
amount ``T`` sets, rather than whatever the cross-section happens to hand you.

Temperature is in **reduced units**: the code divides by ``T · Ē`` with ``Ē`` the
cross-sectional mean energy on that date, so ``T = 1`` means "temperature equal
to the average energy of the book" and means the same thing in 2008 as in 2017.
An absolute ``T`` would silently concentrate in calm years and flatten in
crises — the opposite of what anyone setting it would expect.

Entropy also picks the universe, in its other sense. Each name carries the
Shannon entropy of its daily up/down process over the same window:
``S = -p log₂ p - (1-p) log₂(1-p)`` bits, ``p`` = fraction of up days. One bit is
a fair coin — an efficiently random walk in thermal equilibrium. Below a bit the
series has a persistent direction, which is a bet, not equilibrium, so the
``min_entropy`` floor drops it. This cuts hard uptrends and falling knives
alike: deliberately not a momentum book. The floor's practical bite is the
second kind — a name in a long slide is exactly what a variance-ranked book
would otherwise buy as it "calms down" on the way to zero.

Kept as code rather than a YAML spec: the config engine weights equal or by
ADTV, and this needs an exponential ensemble with a cap (see the "When NOT to
use a spec" list in docs/config_strategies.md).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.core.strategy_base import (
    StrategyBase, ParameterSpec, cap_and_renormalize,
    COMMON_START_DATE, COMMON_END_DATE, COMMON_INITIAL_CAPITAL,
    COMMON_TAX_RATE, COMMON_SLIPPAGE, COMMON_MIN_ADTV, COMMON_REBALANCE_FREQ,
)


def binary_entropy(p):
    """Shannon entropy in bits of a Bernoulli(p) variable, elementwise.

    Peaks at 1 bit for p = 0.5 (a fair coin) and falls to 0 at p ∈ {0, 1}, where
    ``log(0)`` is undefined but the limit ``p log p → 0`` is not — those cells are
    filled with 0.0. NaN inputs (a rolling window that has not filled yet) stay
    NaN, so a warmup row never reads as "zero entropy".
    """
    q = 1.0 - p
    with np.errstate(divide="ignore", invalid="ignore"):
        h = -(p * np.log2(p) + q * np.log2(q))
    return h.mask((p <= 0) | (p >= 1), 0.0)


def directional_entropy(daily_close: pd.DataFrame, window: int) -> pd.DataFrame:
    """Entropy (bits) of each name's daily up/down process over `window` days.

    1.0 = the direction of the next day is a coin flip; lower = a persistent
    drift one way. Measures *order in the sign* only, and is blind to the size
    of the moves — a name can be violent and still sit at a full bit.
    """
    up_day = daily_close.pct_change() > 0
    return binary_entropy(up_day.rolling(window).mean())


def realised_energy(daily_close: pd.DataFrame, window: int) -> pd.DataFrame:
    """The ensemble's energy function: annualised variance of daily returns.

    Variance rather than volatility because variance is what enters the risk
    budget linearly — ``Σ wᵢ σᵢ²`` is the portfolio's average energy — which is
    what makes the Gibbs weights the exact max-entropy solution rather than an
    approximation of one.
    """
    return daily_close.pct_change().rolling(window).var() * 252.0


def boltzmann_weights(energy: pd.Series, temperature: float, cap: float) -> pd.Series:
    """Canonical-ensemble weights ``wᵢ ∝ exp(-Eᵢ / (T · Ē))``, capped at `cap`.

    The exponents are shifted by their own maximum before exponentiating. ``Z``
    is invariant to that shift (it cancels between numerator and denominator),
    but without it a cold name at low ``T`` overflows to ``inf`` and the whole
    row comes back NaN. Degenerate energies (all zero, non-finite, or a single
    name) fall back to equal weight — the ``T → ∞`` limit, which is the right
    answer when there is no dispersion to trade entropy against.
    """
    e = pd.Series(energy, dtype=float)
    mean_e = e.mean()
    if len(e) == 0:
        return e
    if not np.isfinite(mean_e) or mean_e <= 0 or temperature <= 0:
        w = pd.Series(1.0 / len(e), index=e.index)
    else:
        x = -e / (temperature * mean_e)
        w = np.exp(x - x.max())
        w = w / w.sum()
    return cap_and_renormalize(w, cap)


def effective_positions(w: pd.Series) -> float:
    """``exp(S(w))`` — the number of equally-weighted names the book behaves like.

    The perplexity of the weight vector: 20 for 20 names held at 5% each, 1 for
    everything in one name. Reads the concentration a given temperature actually
    bought, in units anyone can check against the book.
    """
    p = w[w > 0]
    return float(np.exp(-(p * np.log(p)).sum())) if len(p) else 0.0


class MaxEntropyStrategy(StrategyBase):
    """Gibbs-ensemble allocation over the coldest names in thermal equilibrium."""

    @property
    def name(self) -> str:
        return "MaxEntropy"

    @property
    def description(self) -> str:
        return (
            "Maximum-entropy (Gibbs/Boltzmann) allocation. Keeps liquid names whose "
            "daily up/down process is still near a coin flip (high Shannon entropy — "
            "no persistent trend either way), takes the coldest by realised variance, "
            "and weights them exp(-variance / temperature): the most diversified book "
            "consistent with a risk budget. Temperature dials continuously from equal "
            "weight (hot) to minimum-variance concentration (cold)."
        )

    def get_parameter_specs(self) -> list[ParameterSpec]:
        return [
            COMMON_START_DATE, COMMON_END_DATE, COMMON_INITIAL_CAPITAL,
            COMMON_TAX_RATE, COMMON_SLIPPAGE, COMMON_MIN_ADTV, COMMON_REBALANCE_FREQ,
            ParameterSpec(
                "temperature", "Temperature", "float", 1.0,
                description="Reduced units (multiples of the book's mean energy). "
                            "High = toward equal weight, low = toward the coldest name.",
                min_value=0.1, max_value=5.0, step=0.1,
            ),
            ParameterSpec(
                "lookback_days", "Lookback (trading days)", "int", 126,
                description="Window for both the variance and the entropy measure",
                min_value=21, max_value=504, step=21,
            ),
            ParameterSpec(
                "top_n", "Number of Stocks", "int", 20,
                description="How many of the coldest eligible names enter the ensemble",
                min_value=5, max_value=60, step=1,
            ),
            ParameterSpec(
                "min_entropy", "Min Entropy (bits)", "float", 0.97,
                description="Equilibrium floor on the daily up/down entropy. 1.0 is a "
                            "fair coin; 0.97 drops names running ~60/40 or stronger.",
                min_value=0.50, max_value=1.0, step=0.01,
            ),
            ParameterSpec(
                "weight_cap", "Max Weight per Name", "float", 0.15,
                description="Per-name ceiling; the excess is redistributed",
                min_value=0.02, max_value=1.0, step=0.01,
            ),
            ParameterSpec(
                "min_price", "Min Price (BRL)", "float", 1.0,
                min_value=0.0, max_value=10.0, step=0.5,
            ),
        ]

    def generate_signals(self, shared_data: dict, params: dict):
        ret = shared_data["ret"]                  # period returns, index = rebalance grid
        adtv = shared_data["adtv"]
        raw_close = shared_data["raw_close"]
        daily_close = shared_data["adj_close"]    # daily total-return adjusted close

        min_adtv = float(params.get("min_adtv", 1_000_000))
        min_price = float(params.get("min_price", 1.0))
        window = int(params.get("lookback_days", 126))
        temperature = float(params.get("temperature", 1.0))
        top_n = int(params.get("top_n", 20))
        min_entropy = float(params.get("min_entropy", 0.97))
        cap = float(params.get("weight_cap", 0.15))

        # Both measures are daily; sample them onto the rebalance calendar.
        energy = self._on_grid(realised_energy(daily_close, window), ret)
        entropy = self._on_grid(directional_entropy(daily_close, window), ret)
        energy[shared_data["has_glitch"] == 1] = np.nan

        tw = pd.DataFrame(0.0, index=ret.index, columns=ret.columns)
        # One row of warmup on top of the daily window, which is already full by
        # the time the grid reaches it — every read below is at row i-1.
        for i in range(2, len(ret)):
            liquid = (adtv.iloc[i - 1] >= min_adtv) & (raw_close.iloc[i - 1] >= min_price)
            e_row = energy.iloc[i - 1][liquid].dropna()
            e_row = e_row[e_row > 0]
            # An untraded name prints a flat forward-filled series: zero variance
            # and zero entropy. Both filters below drop it, but the `> 0` above is
            # what stops it from being handed to the ensemble as the coldest state.
            # A missing entropy reading compares False, so it fails the floor —
            # a name is in equilibrium only if it was measured to be.
            e_row = e_row[entropy.iloc[i - 1].reindex(e_row.index) >= min_entropy]
            if len(e_row) < 5:
                continue

            w = boltzmann_weights(e_row.nsmallest(top_n), temperature, cap)
            for t, wt in w.items():
                tw.iloc[i, tw.columns.get_loc(t)] = wt

        return ret, tw

    @staticmethod
    def _on_grid(daily: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
        """Daily frame → the rebalance grid: last value at or before each date,
        columns aligned to the traded universe."""
        out = daily.reindex(daily.index.union(ret.index)).ffill().reindex(ret.index)
        return out.reindex(columns=ret.columns)
