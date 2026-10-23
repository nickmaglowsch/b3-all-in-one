"""Request models for user-created strategies (spec/06), and their compilation
into the engine's spec dict.

Pydantic validates the statically-checkable rules at the boundary (name, numeric
ranges, rebalance enum, portfolio weights). The engine's validate_spec.py does the
deep signal/factor validation; routes resolves sleeve refs + the cycle guard.
Split out of routes.py, which only needs the compiled dict.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REBALANCE = Literal["ME", "QE", "W-FRI"]
_MARKET = Literal["B3", "US", "CRYPTO"]

# The compiled spec reaches validate_spec.py as one argv element, which the kernel
# caps at 128 KB: past that the exec fails with E2BIG and the save is a 500. These
# bounds keep any spec far inside it, and are generous for a field a person types.
_NAME_MAX, _TEXT_MAX, _SLEEVES_MAX, _REGIME_MAX = 120, 2000, 50, 16 * 1024


class _Finite(BaseModel):
    # json.loads accepts NaN and Infinity, and pydantic's float does too. Stored,
    # they fail the JSONB write or poison every read of the spec afterwards.
    model_config = ConfigDict(allow_inf_nan=False)


class _RankUniverse(_Finite):
    min_adtv: float = Field(default=1_000_000.0, ge=0)
    min_price: float = Field(default=1.0, ge=0)
    smallcap_below_median: bool = False


class _RankSignal(_Finite):
    factor: str | None = Field(default=None, max_length=_NAME_MAX)
    expr: str | None = Field(default=None, max_length=_TEXT_MAX)
    lookback: int | None = Field(default=None, ge=1, le=36)
    shift: int | None = Field(default=None, ge=0)
    range: list[float] | None = Field(default=None, max_length=8)
    apply_glitch: bool | None = None

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.factor) == bool(self.expr):
            raise ValueError("signal needs exactly one of factor / expr")
        return self


class _RankSelection(_Finite):
    top_pct: float | None = Field(default=None, gt=0, le=1)
    top_n: int | None = Field(default=None, ge=1)
    min_names: int = Field(default=1, ge=1)
    min_hold: int | None = Field(default=None, ge=1)
    dedup_roots: bool = False
    sticky: bool | None = None

    @model_validator(mode="after")
    def _one_cut(self):
        if (self.top_pct is None) == (self.top_n is None):
            raise ValueError("selection needs exactly one of top_pct / top_n")
        return self


class RankStrategyIn(_Finite):
    kind: Literal["rank_and_hold"]
    name: str = Field(min_length=1, max_length=_NAME_MAX)
    description: str = Field(default="", max_length=_TEXT_MAX)
    rebalance: _REBALANCE = "ME"
    needs_fundamentals: bool = False
    warmup_pad: int | None = Field(default=None, ge=0)
    weighting: Literal["equal", "adtv", "market_cap"] = "equal"
    # None = B3. Unknown planes are refused here, never silently run as B3.
    market: _MARKET | None = None
    universe: _RankUniverse = Field(default_factory=_RankUniverse)
    signal: _RankSignal
    selection: _RankSelection
    # The macro overlay ({inputs, rules, ...}, docs/config_strategies.md). Shape is
    # the engine's to judge; only its size is ours. It used to be absent here, so
    # pydantic dropped the bench form's regime on every save without a word.
    regime: dict | None = None

    @field_validator("regime")
    @classmethod
    def _regime_shape(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if not isinstance(v.get("rules"), list) or not isinstance(v.get("inputs", {}), dict):
            raise ValueError("regime needs a `rules` list (and an `inputs` mapping)")
        if len(json.dumps(v, allow_nan=False)) > _REGIME_MAX:  # ValueError on NaN too
            raise ValueError("regime too large")
        return v


class PortfolioSleeveIn(_Finite):
    strategy_ref: str = Field(min_length=1, max_length=_NAME_MAX)
    target_pct: float = Field(gt=0, le=100)


class PortfolioStrategyIn(_Finite):
    kind: Literal["fixed_weight"]
    name: str = Field(min_length=1, max_length=_NAME_MAX)
    description: str = Field(default="", max_length=_TEXT_MAX)
    rebalance: _REBALANCE = "QE"
    sleeves: list[PortfolioSleeveIn] = Field(min_length=1, max_length=_SLEEVES_MAX)

    @model_validator(mode="after")
    def _weights_sum(self):
        total = sum(s.target_pct for s in self.sleeves)
        if abs(total - 100.0) > 1e-2:  # == weights sum to 1 ± 1e-4
            raise ValueError(f"portfolio percentages must sum to 100 (got {total:g})")
        return self


StrategyIn = Annotated[RankStrategyIn | PortfolioStrategyIn, Field(discriminator="kind")]


def _compile_rank(m: RankStrategyIn) -> dict:
    spec: dict = {
        "name": m.name.strip(), "kind": "rank_and_hold", "description": m.description,
        "rebalance": m.rebalance, "weighting": m.weighting,
        "universe": m.universe.model_dump(exclude_none=True),
        "signal": m.signal.model_dump(exclude_none=True),
        "selection": m.selection.model_dump(exclude_none=True),
    }
    if m.needs_fundamentals:
        spec["needs_fundamentals"] = True
    if m.market:
        spec["market"] = m.market
    if m.regime:
        spec["regime"] = m.regime
    if m.warmup_pad is not None:
        spec["warmup_pad"] = m.warmup_pad
    return spec


def _compile_portfolio(m: PortfolioStrategyIn) -> dict:
    sleeves = [
        {"asset": "CDI", "weight": s.target_pct / 100.0} if s.strategy_ref == "CASH_CDI"
        else {"strategy": s.strategy_ref, "weight": s.target_pct / 100.0}
        for s in m.sleeves
    ]
    return {
        "name": m.name.strip(), "kind": "fixed_weight", "description": m.description,
        "rebalance": m.rebalance, "sleeves": sleeves,
    }
