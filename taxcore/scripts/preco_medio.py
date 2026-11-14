"""The preço médio walk — one transition function for every surface.

positions.csv (load_b3.derive_positions → portfolio, TLH) and the IRPF report
(realizacao → apuração, Bens e Direitos) walk the same events. They used to do
it with two copies of the rules, and the copies drifted: a same-day round trip
was a day trade on one surface and an averaged-in buy on the other, a split
credited to a flat position was silent on both, and a grupamento that took more
than was held left a phantom cost on one and a negative holding on the other.
Both now call `caminhar`, so a rule changes here or nowhere.

Within a day the walk works per (ticker, broker), not row by row:

- **Day trade** is the quantity bought *and* sold of the same ticker on the same
  day at the same intermediary (Lei 9.959/2000, art. 8º, § 1º, I, as amended by
  Lei 12.350/2010). Its result is the day's average sale price against the day's
  average purchase price — B3 extracts carry no execution time, so the art. 65
  pairing of first buy with first sell cannot be reproduced; the average is exact
  whenever the day's bought and sold quantities match. It never touches the
  swing position's preço médio, whatever the order of the rows.
- What is left — the excess of buys, then the excess of sells — goes through the
  ordinary preço médio walk. Buys first: a same-day sell listed before its buy is
  no longer dropped as a sale of nothing.
- A sale of more than the walk holds (`descoberto`) keeps its **full** value in
  the month's gross — the isenção looks at alienações, not at what the walk
  could cost (Lei 11.033/2004, art. 3º, I) — books a result only for what was
  held, and is flagged. That almost always means the history is incomplete (a
  buy before the extract window, a subscription, a transfer), so the uncovered
  part is never given a cost of zero or a short-sale result against some later,
  unrelated buy.
- Corporate actions (qty_event, amort) apply after the day's trades. A credit to
  a flat position and a debit of more than is held are both **órfãos**: events
  the loaded history cannot explain. The credit is booked anyway, so the
  quantity matches custody, at the only cost the event itself carries; the
  debit closes the position. Either way the walk reports it.
"""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd

_TRADES = ("buy", "sell")
# Below this a quantity is a rounding residue, not a share.
_EPS = 1e-9


class Posicao:
    """Quantity and total cost of one ticker's swing position.

    Two states only: flat (qty = cost = 0) and long. Every transition below
    lands in one of them — no negative holding, no cost left on zero shares.
    """

    def __init__(self) -> None:
        self.qty = 0.0
        self.cost = 0.0

    def _fechar_se_zerada(self) -> None:
        if self.qty <= _EPS:  # closed: no centavo of cost left on zero shares
            self.qty = self.cost = 0.0

    def comprar(self, qty: float, valor: float) -> None:
        self.qty += qty
        self.cost += valor

    def vender(self, qty: float, preco: float) -> tuple[float, float, float]:
        """(quantity covered, preço médio, result) — the rest is descoberto."""
        coberto = min(qty, self.qty)
        avg = self.cost / self.qty if self.qty > 0 else 0.0
        self.cost -= coberto * avg
        self.qty -= coberto
        self._fechar_se_zerada()
        return coberto, avg, coberto * (preco - avg)

    def evento(self, r) -> bool:
        """Apply a qty_event or an amortização. True when it is órfão."""
        if r["kind"] == "amort":
            self.cost = max(0.0, self.cost - r["amount"])
            return False
        sq = float(r["qty"])  # a qty_event
        if sq < 0 and -sq >= self.qty - _EPS:
            # A debit of the whole holding closes it; of more than the holding,
            # the excess was never seen coming in.
            orfao = -sq > self.qty + _EPS
            self.qty = self.cost = 0.0
            return orfao
        # `amount` is the custo atribuído of a bonificação (Lei 9.249/95,
        # art. 10, parágrafo único) and zero for desdobro / grupamento.
        orfao = sq > 0 and self.qty <= _EPS
        self.qty += sq
        self.cost += r["amount"]
        return orfao


def _broker(r) -> str:
    b = r.get("broker", "")
    return b.strip() if isinstance(b, str) else ""


def _por_corretora(trades: pd.DataFrame) -> dict[str, list[float]]:
    """broker → [qty bought, value bought, qty sold, value sold] for one day."""
    acc: dict[str, list[float]] = {}
    for _, r in trades.iterrows():
        a = acc.setdefault(_broker(r), [0.0, 0.0, 0.0, 0.0])
        i = 0 if r["kind"] == "buy" else 2
        a[i] += float(r["qty"])
        a[i + 1] += float(r["amount"])
    return acc


def _venda(date, ticker, broker, qty, preco, avg, pnl, daytrade, descoberto):
    return {"date": date, "ticker": ticker, "broker": broker, "qty": qty,
            "sell_price": preco, "avg_cost": avg,
            "sell_value": (qty + descoberto) * preco, "pnl": pnl,
            "daytrade": daytrade, "descoberto": descoberto}


def _em_ordem(eventos: pd.DataFrame) -> pd.DataFrame:
    """A day's events in walk order: credits, then debits, then amortizações.

    No extract records a time of day. A bonus credited and a fraction debited on
    one date are walked credit-first, so the debit never finds a position it
    would have found a moment later and gets reported as órfão.
    """
    if len(eventos) < 2:
        return eventos
    chave = (eventos["kind"] == "amort") * 2 + (eventos["qty"].astype(float) < 0)
    return eventos.iloc[np.argsort(chave.to_numpy(), kind="stable")]


def realizar_dia(ticker, date, dia: pd.DataFrame, pos: Posicao,
                 orfaos: list[dict] | None = None) -> list[dict]:
    """One day of one ticker: the sales it realised. Órfão events are appended
    to `orfaos` as {date, ticker, qty}."""
    is_trade = dia["kind"].isin(_TRADES)
    out, vendas = [], []
    for broker, (bq, bv, sq, sv) in _por_corretora(dia[is_trade]).items():
        dt = min(bq, sq)
        pb = bv / bq if bq else 0.0
        ps = sv / sq if sq else 0.0
        if dt > 0:
            out.append(_venda(date, ticker, broker, dt, ps, pb, dt * (ps - pb), True, 0.0))
        if bq > dt:
            pos.comprar(bq - dt, (bq - dt) * pb)
        if sq > dt:
            vendas.append((broker, sq - dt, ps))
    for broker, qty, ps in vendas:
        coberto, avg, pnl = pos.vender(qty, ps)
        out.append(_venda(date, ticker, broker, coberto, ps, avg, pnl, False, qty - coberto))
    for _, ev in _em_ordem(dia[~is_trade]).iterrows():
        if pos.evento(ev) and orfaos is not None:
            orfaos.append({"date": date, "ticker": ticker, "qty": float(ev["qty"])})
    return out


def caminhar(tl: pd.DataFrame) -> Iterator[tuple[str, pd.DataFrame, Posicao, list[dict], list[dict]]]:
    """Walk a timeline ticker by ticker: (ticker, its rows, final position,
    sales, órfão events).

    The timeline is build_timeline's shape: ticker, date, kind ∈ {buy, sell,
    qty_event, amort}, qty (unsigned on trades, signed on a qty_event), amount
    (value of a trade, custo atribuído of a qty_event, principal returned on an
    amort) and broker.
    """
    for ticker, group in tl.groupby("ticker", sort=True):
        pos, vendas, orfaos = Posicao(), [], []
        for date, dia in group.groupby("date", sort=True):
            vendas += realizar_dia(ticker, date, dia, pos, orfaos)
        yield ticker, group, pos, vendas, orfaos
