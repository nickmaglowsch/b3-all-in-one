"""Renda fixa contracts — one label table and one cost walk for every surface.

A CDB, LCI, CRI or Tesouro title is read twice: by load_b3 into trades.csv and
positions.csv, and by irpf_2026.renda_fixa_year_end straight off the extract
into Bens e Direitos. Each used to carry its own label classifier and its own
walk, "kept in step" by comment, and they drifted: "JUROS" was income on one
side and an unknown label on the other, and a resgate with no Quantidade was
one whole unit on one side (closing the contract) and a money share on the
other (keeping the rest declared). Both now read `rf_kind` and walk `Contrato`.

A contract's life: aplicada → partly redeemed → redeemed, with two sticky side
states — frozen (custody withdrawn with no value: the paper is still owned) and
incomplete (a resgate or amortização drew on cost the extract never showed
going in; no rendimento is declared after it).
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

# B3's market label for renda fixa (load_b3.RENDA_FIXA_MARKET).
RENDA_FIXA_MARKET = "Renda Fixa"

APLIC, RESGATE, FROZEN, JUROS, AMORT, OTHER = (
    "aplic", "resgate", "frozen", "juros", "amort", "other")

_POR_ROTULO = {
    "APLICAÇÃO": APLIC,
    "APLICAÇÃO/DEPÓSITO": APLIC,
    # Tesouro Direto is booked as a plain Compra / Venda.
    "Compra": APLIC,
    "VENCIMENTO": RESGATE,
    "VENCIMENTO/RESGATE SALDO EM CONTA": RESGATE,
    "RESGATE ANTECIPADO": RESGATE,
    "Resgate": RESGATE,
    "Venda": RESGATE,
    # Coupon paid during the contract's life (CRI/CRA/debênture). Real income,
    # unlike an amortização, which returns principal.
    "PAGAMENTO DE JUROS": JUROS,
    "JUROS": JUROS,
    # Principal returned mid-life: not income and not an exit — it reduces the
    # cost and leaves the quantity alone.
    "AMORTIZAÇÃO": AMORT,
    "Amortização": AMORT,
}
# Labels B3 uses for both legs of a round trip; Entrada/Saída is the only thing
# that separates them.
_PELA_DIRECAO = frozenset({
    "COMPRA / VENDA", "COMPRA/VENDA DEFINITIVA/CESSAO",
    "MDA COMPRA/VENDA DEFINITIVA MERCADO PRIMARIO", "Transferência - Liquidação",
})
# Two unrelated events under one label. With a settlement value it is a
# transfer out that paid — a resgate. With none it is custody frozen (issuer
# under intervention, FGC pending): the paper still exists, is still
# declarable at cost, and booking it as a sale would report a redemption that
# never happened. On an equity the same label is a custody transfer to another
# broker, which is why this table is only ever asked about renda fixa.
RETIRADA = "RETIRADA DE CUSTÓDIA"

# Every label this table has a rule for, and those that move units in or out.
ROTULOS = frozenset(_POR_ROTULO) | _PELA_DIRECAO | {RETIRADA}
CICLO = frozenset(r for r, k in _POR_ROTULO.items() if k in (APLIC, RESGATE)) | _PELA_DIRECAO


def rf_kind(label: str, credito: bool, has_value: bool) -> str:
    """What one Movimentação row does to a renda fixa contract."""
    if label in _PELA_DIRECAO:
        return APLIC if credito else RESGATE
    if label == RETIRADA:
        return RESGATE if has_value else FROZEN
    return _POR_ROTULO.get(label, OTHER)


class Contrato:
    """Units and cost of one renda fixa contract, in preço médio.

    `por_quantidade` says whether the contract's aplicações carry a Quantidade.
    When they do, a resgate that has one takes that share of the units — that is
    preço médio. When it does not, the money decides: the resgate consumes cost
    up to its proceeds. A row with no Quantidade is given the units its money
    buys at the contract's current preço médio, the first one a single unit, so
    trades.csv can still price it and every surface walks the same units.
    """

    def __init__(self, por_quantidade: bool) -> None:
        self.por_quantidade = por_quantidade
        self.qty = 0.0
        self.cost = 0.0
        self.frozen = False
        # True once a resgate or amortização drew on cost this extract never
        # saw — the aplicação predates the downloaded window. Its gain cannot be
        # computed from these rows at all, so no rendimento is booked after it.
        self.incomplete = False
        self.rendimento: dict[int, float] = defaultdict(float)
        self.juros: dict[int, float] = defaultdict(float)

    def informada(self, qtd: float) -> bool:
        """Whether a row's own Quantidade is the one to walk."""
        return self.por_quantidade and qtd > 0

    def aplicar(self, valor: float, qtd: float) -> float:
        if not self.informada(qtd):
            qtd = valor * self.qty / self.cost if self.qty > 0 and self.cost > 0 else 1.0
        self.qty += qtd
        self.cost += valor
        return qtd

    def amortizar(self, valor: float) -> None:
        # Clamped, as preco_medio.Posicao clamps it. An extract whose window
        # opens mid-life shows instalments whose principal was never seen going
        # in, and an unclamped subtraction prints a negative custo de aquisição
        # straight into Bens e Direitos.
        if valor > self.cost:
            self.incomplete = True
        self.cost = max(0.0, self.cost - valor)

    def resgatar(self, valor: float, qtd: float, ano: int) -> float:
        """Book a resgate; returns the units it took out."""
        if self.informada(qtd) and self.qty > 0:
            share = min(qtd, self.qty) / self.qty
        elif self.cost > 0:
            share = min(valor, self.cost) / self.cost
        else:
            # Proceeds against no cost at all: everything this contract was
            # bought with is outside the extract.
            share = 0.0
            self.incomplete = self.incomplete or valor > 0
        consumed = self.cost * share
        if not self.incomplete:
            self.rendimento[ano] += valor - consumed
        self.cost -= consumed
        units = self.qty * share
        self.qty -= units
        return units

    def lancar(self, kind: str, valor: float, qtd: float, ano: int) -> float:
        """Apply one row of the contract's life; returns the units it moved."""
        if kind == APLIC:
            return self.aplicar(valor, qtd)
        if kind == RESGATE:
            return self.resgatar(valor, qtd, ano)
        if kind == AMORT:
            self.amortizar(valor)
        elif kind == JUROS:
            self.juros[ano] += valor
        elif kind == FROZEN:
            # Consumes nothing: a custody withdrawal that never settled paid the
            # holder nothing, so the paper is still owned and still declarable.
            self.frozen = True
        return 0.0


def _num(v) -> float:
    return float(v) if pd.notna(v) else 0.0


def _unidades(linhas: pd.DataFrame, amorts: pd.DataFrame) -> dict:
    """{trade index: units} for one contract's trades, walked with Contrato."""
    compra = linhas["side"] == "Compra"
    c = Contrato(por_quantidade=bool((linhas.loc[compra, "qty"] > 0).any()))
    passos = sorted(
        [(r["date"], 0, i, APLIC if compra[i] else RESGATE, _num(r["amount"]), _num(r["qty"]))
         for i, r in linhas.iterrows()]
        + [(r["date"], 1, None, AMORT, _num(r["amount"]), 0.0) for _, r in amorts.iterrows()],
        key=lambda p: (p[0], p[1]))
    out = {}
    for date, _ordem, i, kind, valor, qtd in passos:
        movidas = c.lancar(kind, valor, qtd, date.year)
        if i is not None:
            # A resgate of a contract with no cost left moved no units: one unit,
            # as before, so positions.csv sees the sale of nothing and flags it.
            out[i] = qtd if c.informada(qtd) else (movidas if movidas > 0 else 1.0)
    return out


def preencher_quantidades(trades: pd.DataFrame,
                          eventos: pd.DataFrame | None = None) -> pd.DataFrame:
    """Give every renda fixa trade with no Quantidade the units Contrato moved.

    Run over the whole, deduplicated book: a contract's aplicação and its
    resgate can sit in two different extracts. Rows of a contract whose
    aplicações carry no Quantidade are all priced in the contract's own units,
    so a resgate that does carry one is not set against units of another kind.
    """
    if not {"qty", "market"} <= set(trades.columns):
        return trades
    # Only a renda fixa row is ever left without units (load_b3._renda_fixa_units).
    falta = trades["qty"].isna() & (trades["market"] == RENDA_FIXA_MARKET)
    if not falta.any():
        return trades
    out = trades.copy()
    amort = (eventos[eventos["event_category"] == "amortization"]
             if eventos is not None and "event_category" in eventos else None)
    novas: dict = {}
    for ticker in out.loc[falta, "ticker"].unique():
        linhas = out[out["ticker"] == ticker]
        amorts = amort[amort["ticker"] == ticker] if amort is not None else linhas.iloc[0:0]
        novas.update(_unidades(linhas, amorts))
    idx = [i for i, q in novas.items() if q != out.at[i, "qty"]]
    out.loc[idx, "qty"] = [novas[i] for i in idx]
    out.loc[idx, "price"] = out.loc[idx, "amount"] / out.loc[idx, "qty"]
    sinal = out.loc[idx, "side"].map({"Compra": 1.0}).fillna(-1.0)
    out.loc[idx, "signed_qty"] = out.loc[idx, "qty"] * sinal
    return out
