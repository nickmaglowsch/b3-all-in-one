"""The live sleeves (PUT /portfolio/sleeves) and what a ticker among them means.

A live sleeve is a strategy, CASH_CDI, or one listed instrument held directly —
a B3 code (PETR4, the ETF IVVB11, a FII, the BDR AAPL34), a US symbol (SPY) or a
crypto pair (BTCUSDT). A strategy sleeve is advised per strategy; a ticker sleeve
*is* its own target weight. Both land in one combined target, priced in reais
from each ticker's own plane (advise_weights --weights --markets).

Two things a US or crypto sleeve cannot have, said rather than glossed over:

  · a current position. The book is built from B3 extracts, so the target treats
    what the tenant holds abroad or on an exchange as zero, and the plan buys
    toward the full weight every time;
  · the B3 tax overlay. Its sales are apurated under other rules (exterior:
    anual, Lei 14.754/2023; cripto: R$35 mil/mês, no loss carry), so they are
    kept out of tlh_plan rather than booked as ações, and the plan says so.
"""
from __future__ import annotations

import datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from platform_app import asset_refs, market_assets
from platform_app.db import get_engine_pool

CASH = "CASH_CDI"
# A close older than this is still a price, but the plan would size a trade on
# it: said on save, not refused (the plane being late is not the tenant's fault).
_STALE_DAYS = 31


class SleeveIn(BaseModel):
    """Exactly one of strategy_ref / ticker. `market` (default B3) goes with a
    ticker, which is folded onto its plane's own symbol here — `IVVB11.SA` and
    `ivvb11` are one sleeve, `btc` is BTCUSDT."""
    strategy_ref: str | None = Field(default=None, min_length=1, max_length=120)
    ticker: str | None = Field(default=None, min_length=1, max_length=24)
    market: asset_refs.Market | None = None
    # NaN passed the sum-to-100 check (every comparison with it is False) and
    # was stored; a negative sleeve is a short position nobody can hold.
    target_pct: float = Field(ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def _one_source(self):
        if (self.strategy_ref is None) == (self.ticker is None):
            raise ValueError("a sleeve needs exactly one of strategy_ref / ticker")
        if self.market and not self.ticker:
            raise ValueError("market goes with a ticker sleeve, and only with it")
        if self.ticker:
            self.market = self.market or "B3"
            self.ticker = asset_refs.canonical(self.ticker, self.market)
        return self

    @property
    def key(self) -> str:
        """What two sleeves may not share: a strategy name, or a market + ticker."""
        return f"{self.market}:{self.ticker}" if self.ticker else self.strategy_ref


def sleeve_out(ref: str | None, ticker: str | None, market: str | None, pct) -> dict:
    """A sleeve as GET/PUT answer it: the same one-of shape the request takes."""
    if ticker:
        return {"ticker": ticker, "market": market, "target_pct": float(pct)}
    return {"strategy_ref": ref, "target_pct": float(pct)}


async def check_tickers(body: list[SleeveIn]) -> list[str]:
    """Refuse (422) a ticker sleeve the plane has never priced; warn about a
    stale close and about what an off-B3 sleeve cannot see. [] without tickers —
    a strategy-only save never touches the plane."""
    refs = [(s.ticker, s.market) for s in body if s.ticker]
    if not refs:
        return []
    try:
        prices = await market_assets.latest_closes(await get_engine_pool(), refs)
    except Exception as e:  # noqa: BLE001 — unchecked is not saved
        raise HTTPException(503, "não foi possível consultar o plano de preços para "
                                 "validar os ativos — tente de novo") from e
    if missing := [asset_refs.label(t, m) for t, m in refs if (t, m) not in prices]:
        raise HTTPException(422, f"sem cotação no plano de preços: {', '.join(missing)}. "
                                 "Escolha um ativo da busca do seletor.")
    today = datetime.date.today()
    warnings = [f"{asset_refs.label(t, m)}: última cotação em {prices[t, m][1].isoformat()}"
                " — o plano usa esse preço" for t, m in refs
                if (today - prices[t, m][1]).days > _STALE_DAYS]
    if off := [asset_refs.label(t, m) for t, m in refs if m != "B3"]:
        warnings.append(off_b3_note(off))
    return warnings


def off_b3_note(labels: list[str]) -> str:
    return (f"{', '.join(labels)}: a posição atual não vem dos seus extratos da B3, "
            "então o alvo x atual e o plano a tratam como zero.")


def split_sleeves(sleeves) -> tuple[float, list, dict[str, float], dict[str, str]]:
    """Stored sleeves → (cash weight, strategy sleeves, {ticker: weight},
    {ticker: market} of the off-B3 tickers advise_weights must price elsewhere)."""
    cash, strategies, tickers, off_b3 = 0.0, [], {}, {}
    for s in sleeves:
        w = float(s.target_pct) / 100.0
        if s.ticker:
            tickers[s.ticker] = tickers.get(s.ticker, 0.0) + w
            if s.market != "B3":
                off_b3[s.ticker] = s.market
        elif s.strategy_ref == CASH:
            cash += w
        else:
            strategies.append((s.strategy_ref, w))
    return cash, strategies, tickers, off_b3


def target_warnings(off_b3: dict[str, str], trades: dict) -> list[str]:
    """What /portfolio/target cannot show for its ticker sleeves."""
    out = []
    if off_b3:
        out.append(off_b3_note([asset_refs.label(t, m) for t, m in sorted(off_b3.items())]))
    if unpriced := trades.get("unpriced_skipped"):
        out.append(f"sem cotação hoje, fora do valor do alvo: {', '.join(unpriced)}")
    return out


def plan_notes(off_b3: dict[str, str], trades: dict) -> list[str]:
    """The rebalance plan's notes for what its trade list and tax overlay leave
    out: off-B3 buys and their tax rules, unpriced names, sub-unit buys."""
    out = []
    if bought := sorted(b["ticker"] for b in trades.get("buys", []) if b["ticker"] in off_b3):
        out.append(f"{', '.join(asset_refs.label(t, off_b3[t]) for t in bought)}: compra "
                   "fora da B3, sem posição atual conhecida; a venda futura é tributada "
                   "por outras regras (exterior: 15% na declaração anual, Lei 14.754/2023; "
                   "cripto: isenção até R$35 mil de vendas no mês, sem compensar prejuízo) "
                   "e não entra no imposto estimado abaixo.")
    if unpriced := trades.get("unpriced_skipped"):
        out.append(f"sem cotação hoje, ficaram fora do plano: {', '.join(unpriced)}")
    if small := trades.get("below_one_lot"):
        out.append(f"o valor a comprar não chega a uma unidade de: {', '.join(small)} "
                   "— aumente o aporte para incluí-los.")
    return out
