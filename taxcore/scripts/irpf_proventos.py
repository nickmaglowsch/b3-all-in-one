"""Two proventos the IRPF report must not file under the obvious line.

  - **Dividends paid through a BDR** come from a foreign issuer. They are not
    the "lucros e dividendos" of a Brazilian company that Lei 9.249/95 art. 10
    exempts, so they leave código 09 and are listed as taxable: carnê-leão in
    the month received, with the tax withheld abroad offset there.
  - **A bonificação em ações** with a custo atribuído raises the cost of the
    position in Bens e Direitos by that amount (Lei 9.249/95 art. 10 parágrafo
    único). The same amount is declared as isento, código 18, or the patrimônio
    grows by a value the declaration does not explain.

Kept out of irpf_2026.py because that module is already past the size the
quality gate lets it grow to.
"""
from __future__ import annotations

from collections.abc import Callable

import pandas as pd


def split_bdr_dividends(dividendos: pd.DataFrame,
                        asset_class: Callable[[str], str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(income dividends that belong in código 09, those paid through a BDR)."""
    income = dividendos[dividendos["event_category"] == "income"]
    bdr = income["ticker"].astype(str).map(asset_class) == "BDR"
    return income[~bdr], income[bdr]


def bonificacoes(yr: pd.DataFrame) -> pd.DataFrame:
    """The year's bonificações with a custo atribuído, as (ticker, qty, valor)."""
    bonus = yr[yr["event_type"].astype(str).str.startswith("Bonificação")
               & (yr["event_category"] == "qty_event")].copy()
    bonus["valor"] = (pd.to_numeric(bonus["qty"], errors="coerce")
                      * pd.to_numeric(bonus["price"], errors="coerce")).round(2)
    return bonus[bonus["valor"] > 0][["date", "ticker", "qty", "price", "valor"]]


def lines(bdr_div: pd.DataFrame, yr: pd.DataFrame, year: int) -> list[str]:
    """Markdown for both, to follow the código 09 table."""
    out = ["\n**Dividendos de BDR — tributáveis, fora do código 09**\n"]
    if bdr_div.empty:
        out.append(f"Nenhum dividendo de BDR registrado em {year}.")
    else:
        out += ["Pagos por emissor estrangeiro: não são os dividendos isentos da Lei "
                "9.249/95. Vão no **carnê-leão** do mês em que foram recebidos "
                "(Rendimentos Tributáveis Recebidos de PF/Exterior), compensando o "
                "imposto retido no exterior. Confira valores e retenção no informe "
                "da corretora.\n",
                f"| BDR | Total R$ em {year} |", "|---|---|"]
        for tk, amt in bdr_div.groupby("ticker")["amount"].sum().items():
            out.append(f"| {tk} | {amt:,.2f} |")
        out.append(f"| **Total** | **{float(bdr_div['amount'].sum()):,.2f}** |")
    bonus = bonificacoes(yr)
    out.append("\n**Código 18 — Incorporação de reservas ao capital / Bonificações em ações**\n")
    if bonus.empty:
        out.append(f"Nenhuma bonificação com custo atribuído em {year}.")
        return out
    out += ["Custo atribuído pela companhia a cada ação bonificada (Lei 9.249/95, "
            "art. 10, parágrafo único). Já está somado ao custo da posição em Bens e "
            "Direitos; declare o mesmo valor aqui, com o CNPJ da companhia.\n",
            "| Data | Ticker | Qtde | Custo/ação | Valor R$ |", "|---|---|---|---|---|"]
    for _, r in bonus.iterrows():
        out.append(f"| {pd.Timestamp(r['date']).date().isoformat()} | {r['ticker']} "
                   f"| {r['qty']:g} | {r['price']:,.2f} | {r['valor']:,.2f} |")
    out.append(f"| **Total código 18** | | | | **{float(bonus['valor'].sum()):,.2f}** |")
    return out
