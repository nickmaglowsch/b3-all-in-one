"""
Strategy Base Class and Parameter Specification
================================================
Defines the abstract base class for all backtest strategies and the
ParameterSpec descriptor used to build dynamic UI forms.

All strategy plugins live in backtests/strategies/ and extend StrategyBase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from backtests.core import markets


# ── Parameter Specification ───────────────────────────────────────────────────

@dataclass(repr=False)
class ParameterSpec:
    """Describes a single strategy parameter for UI form generation.

    Field order is a wire contract: call sites build these positionally, and
    scripts/list_strategies.py serializes them into the frontend's form JSON.
    """

    name: str
    label: str
    param_type: str  # "int", "float", "str", "date", "choice"
    default: Any
    description: str = ""
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    step: Optional[Any] = None
    choices: Optional[list] = None

    # Not the generated repr: a spec carries nine fields and only three of them
    # identify it, so the default would bury the answer in defaults nobody set.
    def __repr__(self) -> str:
        return (
            f"ParameterSpec(name={self.name!r}, type={self.param_type!r}, "
            f"default={self.default!r})"
        )


# ── Selection helpers ─────────────────────────────────────────────────────────

def keep_most_liquid_per_root(scores: pd.Series, adtv_row: pd.Series,
                              market: str = markets.DEFAULT) -> pd.Series:
    """Collapse multi-share-class companies to a single ticker before ranking.

    For each company root, keep only the ticker with the highest ADTV (e.g. PETR4
    over PETR3). Company fundamentals are broadcast onto every share class, so
    without this a fundamentals ranker could select PETR3 AND PETR4 as two
    "separate" names — double exposure to one company. Ties and missing-ADTV rows
    fall back to first-seen. Returns ``scores`` unchanged when empty; the returned
    order is arbitrary (callers re-sort via nlargest/nsmallest).

    The root width comes from the market. B3's is four characters; off B3 there
    are no share-class roots and the grouping is skipped entirely — a fixed
    four-char prefix would merge GOOG with GOOGL and BTCUSDT with BTCUSDC, which
    are not two classes of anything.
    """
    root_len = markets.get(market).root_len
    if scores.empty or root_len is None:
        return scores
    roots = [str(t)[:root_len] for t in scores.index]
    liq = adtv_row.reindex(scores.index).fillna(-1.0)   # unknown ADTV -> lowest
    keep = liq.groupby(roots).idxmax()                  # one ticker per root
    return scores.loc[keep.values]


def cap_and_renormalize(w: pd.Series, cap: float) -> pd.Series:
    """Clip weights at ``cap`` and redistribute the excess across the uncapped
    names, repeating until nothing exceeds the cap (standard cap-and-redistribute).

    Shared by every weighting scheme that needs a per-name ceiling — inverse-vol
    (ibov_low_vol) and the Boltzmann ensemble (max_entropy). Redistribution is
    proportional, so it preserves the *shape* of whatever the scheme produced
    among the names still below the cap.

    A capped name is frozen, not merely clipped. Clipping alone does not
    terminate: a name sitting exactly at the cap is not *above* it, so the next
    round counts it as a recipient and tops it straight back up, and the loop can
    run out of iterations with weights still over the ceiling. Freezing retires
    at least one name per round, so this converges in at most ``len(w)`` rounds.

    When the ceiling is unreachable (``cap * len(w) < 1`` — no weight vector
    summing to 1 can respect it) the flattest book that exists is equal weight,
    so that is what comes back.
    """
    w = w.astype(float).copy()
    if len(w) == 0:
        return w
    if cap * len(w) <= 1.0:
        return pd.Series(1.0 / len(w), index=w.index)

    frozen = pd.Series(False, index=w.index)
    for _ in range(len(w)):
        over = (w > cap) & ~frozen
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        frozen |= over
        free = ~frozen
        if not free.any() or w[free].sum() == 0:
            break
        w[free] += excess * w[free] / w[free].sum()
    return w


def dedup_target_weights(tw: pd.DataFrame, adtv: pd.DataFrame,
                         market: str = markets.DEFAULT) -> pd.DataFrame:
    """Global safety net applied to EVERY strategy's target weights before the sim.

    For each rebalance row, weights on tickers sharing a company root (e.g. PETR3
    + PETR4) are merged onto that row's highest-ADTV ticker, so no company is ever
    held via two share classes. Merging (not dropping) preserves the row's weight
    sum — no vaporized weight. Special assets like CDI_ASSET / IBOV have unique
    roots and are never touched. No-op when a strategy already holds one ticker
    per company (e.g. rankers that pre-dedup their pool).

    Off B3 this is a no-op rather than a hazard. It runs unconditionally on every
    strategy's weights, and a fixed four-char prefix there would silently merge
    GOOG into GOOGL and BTCUSDT into BTCUSDC — collapsing two real positions into
    one and moving the weight with them.
    """
    root_len = markets.get(market).root_len
    if root_len is None:
        return tw
    groups: dict[str, list] = {}
    for c in tw.columns:
        groups.setdefault(str(c)[:root_len], []).append(c)
    dup_groups = {r: cols for r, cols in groups.items() if len(cols) > 1}
    if not dup_groups:
        return tw

    out = tw.copy()
    for cols in dup_groups.values():
        sub = out[cols]
        multi = (sub != 0).sum(axis=1) > 1          # rows holding >1 class of this company
        for dt in out.index[multi]:
            held = sub.loc[dt]
            held = held[held != 0]
            liq = adtv.loc[dt, held.index] if dt in adtv.index else pd.Series(dtype=float)
            keep = liq.reindex(held.index).fillna(-1.0).idxmax()
            out.loc[dt, held.index] = 0.0
            out.loc[dt, keep] = held.sum()          # consolidate exposure, keep sum
    return out


# ── Common Parameter Specs ────────────────────────────────────────────────────
# Reusable specs that most strategies include.

COMMON_START_DATE = ParameterSpec(
    "start_date", "Start Date", "date", "2005-01-01",
    description="Backtest start date",
)
COMMON_END_DATE = ParameterSpec(
    "end_date", "End Date", "date", "today",
    description="Backtest end date ('today' for current date)",
)
COMMON_INITIAL_CAPITAL = ParameterSpec(
    "initial_capital", "Initial Capital (BRL)", "float", 100_000.0,
    description="Starting portfolio value in BRL. 0 = pure DCA, the book is "
                "built entirely from the monthly buy-ins.",
    min_value=0.0, step=10_000.0,
)
COMMON_TAX_RATE = ParameterSpec(
    "tax_rate", "Tax Rate", "float", 0.15,
    description="Capital gains tax rate applied to realised profits",
    min_value=0.0, max_value=0.30, step=0.01,
)
COMMON_SLIPPAGE = ParameterSpec(
    "slippage", "Slippage", "float", 0.001,
    description="Round-trip transaction cost fraction per trade",
    min_value=0.0, max_value=0.01, step=0.0005,
)
COMMON_MIN_ADTV = ParameterSpec(
    "min_adtv", "Min ADTV (BRL)", "float", 1_000_000.0,
    description="Minimum average daily trading volume (BRL) for a stock to be eligible",
    min_value=0.0, step=100_000.0,
)
COMMON_REBALANCE_FREQ = ParameterSpec(
    "rebalance_freq", "Rebalance Frequency", "choice", "ME",
    description="Portfolio rebalancing frequency",
    choices=["ME", "QE", "W-FRI"],
)
COMMON_CONTRIBUTION = ParameterSpec(
    "contribution", "Monthly Buy-In (BRL)", "float", 0.0,
    description="Periodic buy-in (aporte) added every calendar month and "
                "allocated by the rebalance — the holding farthest below its "
                "target gets the most. Negative = withdrawal. 0 = lump sum only.",
    step=500.0,
)
def market_param(default: str = markets.DEFAULT) -> "ParameterSpec":
    """The market selector every spec-driven strategy exposes.

    A choice, not free text, so the frontend form renders it and a typo cannot
    reach the loader. `validate_spec` rejects an unknown market too — this is the
    UI half of the same guard.
    """
    return ParameterSpec(
        "market", "Market", "choice", default,
        description="Which market plane to trade. Prices are converted to BRL "
                    "for every market — the numeraire is the holder's currency.",
        choices=markets.keys(),
    )


# There is deliberately no COMMON_MONTHLY_SALES_EXEMPTION. The isenção is the
# asset class's rule, not a strategy knob, and shipping it as a parameter with a
# hard-coded 20_000 default meant any strategy declaring it wrote that literal
# into `params` — where it beat the market's own value and charged a crypto or
# US book the isenção of ações. `markets.resolve_tax` is the one source now.


# ── Abstract Base Class ───────────────────────────────────────────────────────

class StrategyBase(ABC):
    """Abstract base class for all backtest strategy plugins."""

    # Every hand-written Python strategy inherits B3 with no edit. A spec-driven
    # one overrides it from its `market:` key.
    market: str = markets.DEFAULT

    @property
    @abstractmethod
    def name(self) -> str:
        """Short unique name for the strategy (e.g. 'LowVol')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description shown in the UI."""
        ...

    @abstractmethod
    def get_parameter_specs(self) -> list[ParameterSpec]:
        """
        Return the list of configurable parameters with their UI specs.
        Used by the dynamic parameter form component.
        """
        ...

    def get_default_parameters(self) -> dict:
        """Return {param_name: default_value} for all parameters."""
        return {spec.name: spec.default for spec in self.get_parameter_specs()}

    @abstractmethod
    def generate_signals(
        self, shared_data: dict, params: dict
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Generate trading signals and target weights.

        Args:
            shared_data: Dict of precomputed DataFrames from build_shared_data().
            params: Parameter dict (from get_default_parameters or UI form).

        Returns:
            Tuple of (returns_matrix, target_weights).
            Both are DataFrames with DatetimeIndex rows and ticker columns.
            target_weights rows should sum to <= 1.0.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
