"""Request models for user-created strategies (spec/06), and their compilation
into the engine's spec dict.

Pydantic validates the statically-checkable rules at the boundary (name, numeric
ranges, rebalance enum, portfolio weights, sleeve shape). The engine's
validate_spec.py does the deep signal/factor validation and checks ticker sleeves
against the price plane; check_sleeve_refs resolves strategy refs + the cycle
guard against the gallery routes hands it. Split out of routes.py, which only
needs the compiled dict.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator,
)

from platform_app import asset_refs

_REBALANCE = Literal["ME", "QE", "W-FRI"]
_MARKET = Literal["B3", "US", "CRYPTO"]

# The compiled spec reaches validate_spec.py on stdin (routes._validate_spec_engine),
# so argv's 128 KB cap no longer bounds it; these keep a spec — and the JSONB row —
# a sane size, and are generous for a field a person types.
_NAME_MAX, _TEXT_MAX, _SLEEVES_MAX, _REGIME_MAX = 120, 2000, 50, 16 * 1024
_VARS_MAX, _LEGS_MAX, _KEYWORDS_MAX, _KEYWORD_LEN = 16, 8, 20, 80
# What a ticker is, per market, lives in asset_refs (B3 codes, US symbols,
# crypto pairs); a saved carteira's ticker sleeves are B3 codes.
_TICKER_MAX = 16
_UNIVERSE_TICKERS_MAX = 50    # engine/scripts/validate_spec.py says the same
# /estrategia's sleeve editor keys a ticker row with this prefix
# (frontend/lib/portfolioSleeves.ts rowKey). Refused at the start of a strategy
# name so no strategy row can ever share a ticker row's key.
_RESERVED_PREFIX = "@ticker:"


def _no_reserved_prefix(name: str) -> str:
    if name.strip().lower().startswith(_RESERVED_PREFIX):
        raise ValueError(f"names may not start with {_RESERVED_PREFIX!r} (reserved)")
    return name


_Name = Annotated[str, Field(min_length=1, max_length=_NAME_MAX),
                  AfterValidator(_no_reserved_prefix)]


class _Finite(BaseModel):
    # json.loads accepts NaN and Infinity, and pydantic's float does too. Stored,
    # they fail the JSONB write or poison every read of the spec afterwards.
    model_config = ConfigDict(allow_inf_nan=False)


_Keywords = Annotated[list[Annotated[str, Field(min_length=1, max_length=_KEYWORD_LEN)]],
                      Field(max_length=_KEYWORDS_MAX)]


class _Sectors(_Finite):
    """CVM sector keywords (case-insensitive substrings of SETOR_ATIV)."""
    include: _Keywords = []
    exclude: _Keywords = []


class _RankUniverse(_Finite):
    min_adtv: float = Field(default=1_000_000.0, ge=0)
    min_price: float = Field(default=1.0, ge=0)
    smallcap_below_median: bool = False
    sectors: _Sectors | None = None
    # An allow-list: the universe is these codes only (["BTCUSDT"], a basket of
    # ETFs). Spelled for the spec's market; whether that plane prices each one is
    # the engine's to say on save (validate_spec), as for a ticker sleeve.
    tickers: Annotated[list[Annotated[str, Field(min_length=1, max_length=_TICKER_MAX)]],
                       Field(min_length=1, max_length=_UNIVERSE_TICKERS_MAX)] | None = None


class _RankLeg(_Finite):
    factor: str | None = Field(default=None, max_length=_NAME_MAX)
    expr: str | None = Field(default=None, max_length=_TEXT_MAX)
    weight: float = 1.0
    transform: Literal["rank", "zscore", "raw"] = "rank"

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.factor) == bool(self.expr):
            raise ValueError("a leg needs exactly one of factor / expr")
        return self


class _RankSignal(_Finite):
    factor: str | None = Field(default=None, max_length=_NAME_MAX)
    expr: str | None = Field(default=None, max_length=_TEXT_MAX)
    legs: list[_RankLeg] | None = Field(default=None, min_length=1, max_length=_LEGS_MAX)
    coverage: Literal["all", "any"] | None = None
    # Named sub-expressions the expr, the legs and weight_expr can read. Whether
    # each name is legal (an identifier, not a function) is the engine's to say.
    vars: dict[Annotated[str, Field(max_length=64)],
               Annotated[str, Field(max_length=_TEXT_MAX)]] | None = None
    lookback: int | None = Field(default=None, ge=1, le=36)
    shift: int | None = Field(default=None, ge=0)
    range: list[float] | None = Field(default=None, max_length=8)
    apply_glitch: bool | None = None

    @model_validator(mode="after")
    def _one_source(self):
        if sum(map(bool, (self.factor, self.expr, self.legs))) != 1:
            raise ValueError("signal needs exactly one of factor / expr / legs")
        if self.vars is not None and len(self.vars) > _VARS_MAX:
            raise ValueError(f"signal.vars: at most {_VARS_MAX} names")
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


_Months = Annotated[list[Annotated[int, Field(ge=1, le=12)]],
                    Field(min_length=1, max_length=12)]


def _check_months(months: list[int] | None, rebalance: str) -> list[int] | None:
    """Calendar months to trade in (config_strategy.only_in_months); a QE grid
    only has rows in quarter-end months, so any other would never trade."""
    if months is None:
        return None
    months = sorted(set(months))
    if rebalance == "QE" and (off := [m for m in months if m % 3]):
        raise ValueError(f"rebalance_months {off}: a quarterly grid only has rows in "
                         "months 3, 6, 9 and 12 — use rebalance ME for other months")
    return months


class RankStrategyIn(_Finite):
    kind: Literal["rank_and_hold"]
    name: _Name
    description: str = Field(default="", max_length=_TEXT_MAX)
    rebalance: _REBALANCE = "ME"
    rebalance_months: _Months | None = None
    needs_fundamentals: bool = False
    warmup_pad: int | None = Field(default=None, ge=0)
    weighting: Literal["equal", "adtv", "market_cap", "expr"] = "equal"
    weight_expr: str | None = Field(default=None, max_length=_TEXT_MAX)
    max_weight: float | None = Field(default=None, gt=0, le=1)
    # Each held name keeps clip(value, 0, 1) of its weight, the rest in CDI — e.g.
    # "ath_drawdown" for "hold more the further it is below its own high".
    exposure_expr: str | None = Field(default=None, min_length=1, max_length=_TEXT_MAX)
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

    @model_validator(mode="after")
    def _weights_and_months(self):
        if (self.weighting == "expr") != bool(self.weight_expr):
            raise ValueError("weight_expr goes with weighting `expr`, and only with it")
        self.rebalance_months = _check_months(self.rebalance_months, self.rebalance)
        return self


class PortfolioSleeveIn(_Finite):
    """One sleeve: a strategy (a built-in, the user's own rank_and_hold, or
    CASH_CDI) or a single listed B3 instrument — the two sleeve forms the built-in
    portfolios use. Exactly one of the two."""
    strategy_ref: str | None = Field(default=None, min_length=1, max_length=_NAME_MAX)
    ticker: str | None = Field(default=None, min_length=1, max_length=_TICKER_MAX)
    # Said so a US or crypto asset is refused for what it is, not as a malformed
    # B3 code. None and "B3" are the same sleeve.
    market: _MARKET | None = None
    target_pct: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _one_source(self):
        if (self.strategy_ref is None) == (self.ticker is None):
            raise ValueError("a sleeve needs exactly one of strategy_ref / ticker")
        if self.market and not self.ticker:
            raise ValueError("market goes with a ticker sleeve, and only with it")
        if self.ticker:
            self.ticker = _b3_sleeve_ticker(self.ticker, self.market or "B3")
            self.market = None
        return self


def _b3_sleeve_ticker(raw: str, market: str) -> str:
    """Normalised to the built-ins' own spelling (`IVVB11.SA`), so a user sleeve
    and a built-in one compile to the same thing. Whether the plane has prices
    for it is the engine's to say (validate_spec), on save.

    B3 only. A carteira backtests through FixedWeight, which reads a ticker
    sleeve off the B3 plane and books every sale on the one B3 tax ledger — a
    US sale (anual, Lei 14.754) or a crypto one (R$35 mil, no carry) cannot be
    apurated there. The live sleeves take them; a saved carteira refuses them.
    """
    if market != "B3":
        raise ValueError(
            f"{asset_refs.label(raw.strip().upper(), market)} não pode entrar numa "
            "carteira salva: o backtest de carteiras roda no plano da B3 e apura o "
            "imposto pelas regras da B3. Ativos dos EUA e cripto entram só nos "
            "sleeves ativos (alvo e rebalanceamento) em /estrategia.")
    return f"{asset_refs.b3_code(raw)}.SA"


class PortfolioStrategyIn(_Finite):
    kind: Literal["fixed_weight"]
    name: _Name
    description: str = Field(default="", max_length=_TEXT_MAX)
    rebalance: _REBALANCE = "QE"
    rebalance_months: _Months | None = None
    sleeves: list[PortfolioSleeveIn] = Field(min_length=1, max_length=_SLEEVES_MAX)

    @model_validator(mode="after")
    def _weights_sum(self):
        self.rebalance_months = _check_months(self.rebalance_months, self.rebalance)
        total = sum(s.target_pct for s in self.sleeves)
        if abs(total - 100.0) > 1e-2:  # == weights sum to 1 ± 1e-4
            raise ValueError(f"portfolio percentages must sum to 100 (got {total:g})")
        # One sleeve per instrument: the blend gives each ticker one column, so
        # two sleeves of it would have to be merged anyway — say so instead.
        tickers = [s.ticker for s in self.sleeves if s.ticker]
        if dup := sorted({t for t in tickers if tickers.count(t) > 1}):
            raise ValueError(f"ticker {dup[0]} is in more than one sleeve — merge them")
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
    for key in ("weight_expr", "exposure_expr", "max_weight", "rebalance_months"):
        if getattr(m, key) is not None:
            spec[key] = getattr(m, key)
    return spec


def _compile_sleeve(s: PortfolioSleeveIn) -> dict:
    weight = s.target_pct / 100.0
    if s.ticker:
        return {"ticker": s.ticker, "weight": weight}
    if s.strategy_ref == "CASH_CDI":
        return {"asset": "CDI", "weight": weight}
    return {"strategy": s.strategy_ref, "weight": weight}


def _compile_portfolio(m: PortfolioStrategyIn) -> dict:
    # The built-in blends' spec shape, sleeve for sleeve, so a user portfolio runs
    # down the same FixedWeight path — CDI parking, then the same tax engine.
    # park_in_cdi_until_live is the engine's default too; it is spelt out because
    # a ticker sleeve's weight sitting in CDI before its first price is part of
    # what the stored spec means, and the reader of it should not have to know.
    return {
        "name": m.name.strip(), "kind": "fixed_weight", "description": m.description,
        "rebalance": m.rebalance, "park_in_cdi_until_live": True,
        "sleeves": [_compile_sleeve(s) for s in m.sleeves],
        **({"rebalance_months": m.rebalance_months} if m.rebalance_months else {}),
    }


def off_b3_refusal(ref: str, market: str) -> HTTPException:
    """A portfolio and the live target run on the B3 plane (FixedWeight, and
    advise_weights, load B3), so a US or crypto strategy inside one would be fed
    another market's names: an empty book — or, with universe.tickers, a run
    that fails. Refused where it is chosen, not discovered at run time."""
    return HTTPException(422, f"sleeve {ref!r} runs on the {market} plane; portfolios "
                              "and the live target run on B3 — backtest it on its own")


def off_b3_refs(builtins: list[dict], rows) -> dict[str, str]:
    """{name: market} of the built-ins and user rank strategies that do not run
    on B3 — the refs a portfolio sleeve may not take."""
    out = {s["name"]: s["market"] for s in builtins if (s.get("market") or "B3") != "B3"}
    return out | {r.name: r.spec["market"] for r in rows
                  if r.kind == "rank_and_hold" and (r.spec or {}).get("market", "B3") != "B3"}


def check_live_refs(refs: list[str], gallery: dict[str, dict]) -> None:
    """The live target's sleeves: CASH_CDI or a gallery strategy that runs on B3
    (advise_weights loads the B3 plane, as a portfolio does)."""
    for ref in refs:
        if ref != "CASH_CDI" and ref not in gallery:
            raise HTTPException(422, f"unknown strategy_ref: {ref}")
        if (market := gallery.get(ref, {}).get("market") or "B3") != "B3":
            raise off_b3_refusal(ref, market)


def check_sleeve_refs(m: PortfolioStrategyIn, *, resolvable: set[str],
                      user_portfolios: set[str],
                      off_b3: dict[str, str] | None = None) -> None:
    """Every strategy_ref names something the gallery can run, on B3 (`off_b3`:
    {name: market} of the ones that run elsewhere), and never another user
    portfolio (or this one): portfolios do not nest, so no cycle can form.
    Ticker sleeves are not refs — the engine checks those against the plane."""
    for s in m.sleeves:
        ref = s.strategy_ref
        if ref is None:
            continue
        if ref in user_portfolios or ref == m.name.strip():
            raise HTTPException(422, f"sleeve {ref!r}: a portfolio cannot reference "
                                     f"another user portfolio (would recurse)")
        if off_b3 and ref in off_b3:
            raise off_b3_refusal(ref, off_b3[ref])
        if ref not in resolvable:
            hint = (" (a listed instrument goes in `ticker`, not `strategy_ref`)"
                    if asset_refs.B3_TICKER.fullmatch(
                        ref.strip().upper().removesuffix(".SA")) else "")
            raise HTTPException(422, f"unknown sleeve strategy_ref: {ref!r}{hint}")


def live_sleeves(spec: dict) -> list[dict]:
    """A saved portfolio as the live target's sleeve bodies (PUT /portfolio/sleeves).

    One for one: a strategy stays a strategy, CDI is CASH_CDI, and a ticker
    sleeve — always a B3 code in a saved carteira — is the same instrument as a
    live ticker sleeve, which PUT /portfolio/sleeves prices before it is stored.
    """
    out: list[dict] = []
    for sl in spec.get("sleeves", []):
        pct = round(float(sl["weight"]) * 100.0, 6)
        if "ticker" in sl:
            out.append({"ticker": sl["ticker"], "market": "B3", "target_pct": pct})
        else:
            ref = "CASH_CDI" if sl.get("asset") == "CDI" else sl["strategy"]
            out.append({"strategy_ref": ref, "target_pct": pct})
    return out
