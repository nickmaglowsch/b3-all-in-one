"""How the rebalance plan (and the target's drift) treat US and crypto sleeves.

The book is built from B3 extracts, so an off-B3 sleeve's current position is
unknown. Counted as zero, every plan would re-buy its full weight — and, with
buy_only_laggards off, pay for it by selling B3 holdings, a taxable sale
repeated every cycle until the B3 book was drained into assets the platform
cannot even see. So the target is split in two (split_target):

  · the B3 book is rebalanced among the B3 sleeves alone, its weights scaled to
    the book, with the off-B3 weight treated as held outside it — it causes no
    B3 sell and no B3 buy;
  · the off-B3 sleeves are bought only with the aporte: new_cash is shared
    between the two by target weight (Σ off-B3 weight of it goes abroad), so an
    off-B3 buy never exceeds the aporte and nothing is sold to fund one. No
    aporte, no off-B3 buy.

The plan's notes say so first, then what the trade list and the B3 tax overlay
leave out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from platform_app import asset_refs


@dataclass(frozen=True)
class TargetSplit:
    b3: dict[str, float]           # the B3 book's target, scaled to the book
    cash: float                    # CASH_CDI's weight within the B3 book
    off: dict[str, float] = field(default_factory=dict)  # off-B3 weights in the whole target
    # No B3 sleeve at all: the B3 book is left as it is (nothing to rebalance
    # it to), rather than sold down to cash.
    hold_b3: bool = False

    @property
    def off_weight(self) -> float:
        return sum(self.off.values())

    def aporte(self, new_cash: float) -> tuple[float, float]:
        """new_cash → (the B3 book's share, the off-B3 sleeves' share)."""
        off = new_cash * self.off_weight
        return new_cash - off, off


def split_target(combined: dict[str, float], cash_weight: float,
                 off_b3: dict[str, str]) -> TargetSplit:
    """The combined target as the B3 book's own target plus the off-B3 part."""
    off = {t: w for t, w in combined.items() if t in off_b3 and w > 0}
    rest = 1.0 - sum(off.values())
    if not off:
        return TargetSplit(dict(combined), cash_weight)
    if rest <= 1e-9:
        return TargetSplit({}, 0.0, off, hold_b3=True)
    b3 = {t: w / rest for t, w in combined.items() if t not in off_b3}
    return TargetSplit(b3, cash_weight / rest, off)


def drift_rows(split: TargetSplit, current: dict[str, float], nav: float,
               off_b3: dict[str, str]) -> list[dict]:
    """/portfolio/target's drift: the B3 book against its own target, then each
    off-B3 sleeve with its weight in the whole target and no current position
    or drift (`outside_b3`) — unknown, not zero."""
    rows = []
    for t in sorted(set(split.b3) | set(current)):
        cw = float(current.get(t, 0.0))
        tw = cw if split.hold_b3 else float(split.b3.get(t, 0.0))
        rows.append({"ticker": t, "target_w": tw, "current_w": cw,
                     "drift_w": tw - cw, "drift_brl": (tw - cw) * nav})
    rows += [{"ticker": t, "market": off_b3[t], "target_w": w, "current_w": None,
              "drift_w": None, "drift_brl": None, "outside_b3": True}
             for t, w in sorted(split.off.items())]
    return rows


def cap_buys(buys: list[dict], budget: float) -> tuple[list[dict], list[str]]:
    """Greedy top-up: deploy `budget` BRL into the B3 buys, biggest gap first,
    trimming the last one to whole shares. Used by buy_only_laggards so the plan
    only spends the cash actually available (the B3 share of new_cash + harvest
    proceeds), never phantom trim proceeds. Off-B3 buys never pass here: they
    are sized from their own share of the aporte (split_target).

    Returns (kept, unfunded): `unfunded` names every buy the budget ran out
    before — skipped, or reached with less than one share left — so the plan can
    say so instead of dropping them."""
    kept: list[dict] = []
    unfunded: list[str] = []
    for b in sorted(buys, key=lambda x: -float(x.get("est_brl", 0.0))):
        brl = float(b.get("est_brl", 0.0))
        price = float(b.get("est_price", 0.0))
        qty = math.floor(budget / price) if price > 0 and budget > 0 else 0
        if brl <= budget:
            kept.append(b)
            budget -= brl
        elif qty > 0:
            kept.append({**b, "qty": qty, "est_brl": qty * price})
            budget -= qty * price
        else:
            unfunded.append(b["ticker"])
    return kept, unfunded


def target_warnings(split: TargetSplit, off_b3: dict[str, str], trades: dict) -> list[str]:
    """What /portfolio/target cannot show for its ticker sleeves."""
    out = []
    if split.off:
        names = ", ".join(f"{asset_refs.label(t, off_b3[t])} {w:.0%}"
                          for t, w in sorted(split.off.items()))
        out.append(f"Fora da B3 no alvo: {names}. A posição atual deles não vem dos seus "
                   "extratos: o alvo x atual compara só a carteira da B3, com esse peso "
                   "tratado como mantido fora dela.")
    if unpriced := trades.get("unpriced_skipped"):
        out.append(f"sem cotação hoje, fora do valor do alvo: {', '.join(unpriced)}")
    return out


def funding_note(split: TargetSplit, off_b3: dict[str, str], new_cash: float) -> str | None:
    """The first note of a plan with off-B3 sleeves: where their money comes from."""
    if not split.off:
        return None
    names = ", ".join(asset_refs.label(t, off_b3[t]) for t in sorted(split.off))
    head = (f"Ativos fora da B3 ({names}) entram só com aporte: o plano nunca vende "
            "ativos da B3 para comprá-los, e a carteira da B3 é rebalanceada entre os "
            "sleeves da B3, com o peso deles tratado como mantido fora.")
    if new_cash <= 0:
        return head + " Sem aporte, nenhuma compra deles neste plano."
    _b3, off = split.aporte(new_cash)
    return head + (f" Deste aporte, R$ {_brl(off)} vão para eles ({split.off_weight:.0%}, "
                   "o peso deles no alvo).")


def _brl(v: float) -> str:
    """1234.5 → "1.234,50"."""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def plan_notes(split: TargetSplit, off_b3: dict[str, str], new_cash: float,
               trades: dict, unfunded: list[str]) -> list[str]:
    """The rebalance plan's notes: how off-B3 sleeves are funded, their tax rules,
    unpriced names, a new position under one unit, a budget that ran out.
    `trades` merges the B3 book's and the off-B3 buys' engine answers."""
    out = [n for n in [funding_note(split, off_b3, new_cash)] if n]
    if bought := sorted(b["ticker"] for b in trades.get("buys", []) if b["ticker"] in off_b3):
        out.append(f"{', '.join(asset_refs.label(t, off_b3[t]) for t in bought)}: compra "
                   "fora da B3; a venda futura é tributada por outras regras (exterior: "
                   "15% na declaração anual, Lei 14.754/2023; cripto: isenção até R$35 mil "
                   "de vendas no mês, sem compensar prejuízo) e não entra no imposto "
                   "estimado abaixo.")
    if unpriced := trades.get("unpriced_skipped"):
        out.append(f"sem cotação hoje, ficaram fora do plano: {', '.join(unpriced)}")
    if small := trades.get("below_one_lot"):
        out.append(f"o valor a comprar não chega a uma unidade de: {', '.join(small)} "
                   "— aumente o aporte para incluí-los.")
    if unfunded:
        out.append(f"o aporte acabou antes de: {', '.join(unfunded)} — ficaram sem compra "
                   "neste plano.")
    return out
