"""What the TLH planner decides per bucket, asked of the apuração itself.

The planner used to rebuild the month's tax in four hand-written branches — the
isenção, a month already at a loss, a carry that covers the gain, a harvest —
each re-implementing a rule `apuracao.monthly_apuracao` already states, and only
over the ações and FII lines. A BDR or ETF de renda variável sale shares the
operações comuns carry with ações (IN RFB 1.585/2015, art. 64) and owes 15%
with no isenção; the planner never saw it.

Now the month is put to `monthly_apuracao` as it would close — with the proposed
sells, and again with the harvest — and the status is read off what it says.
"""
from __future__ import annotations

from enum import Enum

import pandas as pd

from scripts.apuracao import monthly_apuracao


class Status(str, Enum):
    """A bucket's verdict. The values are the strings the --json contract has
    always carried; the platform and the report compare against them."""
    NENHUM = "Nenhum TLH necessário"
    RECOMENDADO = "TLH recomendado"
    SEM_CANDIDATOS = "TLH desejável mas sem candidatos"

    __str__ = str.__str__  # f-strings print the value, on every Python


# TLH bucket → the apuração lines it covers. "AÇÃO" is the operações comuns
# bucket: the ações line, whose gross the isenção reads, and the BDR/ETF line.
LINHAS = {"AÇÃO": ("AÇÃO", "BDR_ETF"), "FII": ("FII",)}
# asset_class → (TLH bucket, grade column a sale of it lands in).
DESTINO = {"AÇÃO": ("AÇÃO", "AÇÃO"), "BDR": ("AÇÃO", "BDR"),
           "ETF_RV": ("AÇÃO", "ETF_RV"), "FII": ("FII", "FII")}


def com_vendas(grade: pd.DataFrame, vendas: list[dict]) -> pd.DataFrame:
    """The grade with `vendas` ({cls, sell_value, pnl}) added to its last month."""
    g = grade.copy()
    i = g.index[-1]
    for v in vendas:
        if v["cls"] in DESTINO:
            col = DESTINO[v["cls"]][1]
            g.at[i, f"{col}_gross"] += v["sell_value"]
            g.at[i, f"{col}_pnl"] += v["pnl"]
    return g


def apurar_mes(grade: pd.DataFrame, bucket: str) -> dict:
    """The bucket's last month as monthly_apuracao closes it.

    gross is the isenção base (the bucket's first line); pnl, ir and base add
    up its lines. carry_inicial is the pool the month opened with, carry_after
    the pool it closes with, and carry_used what the month drew from the
    former — never what one of its own lines lost and another spent.
    """
    linhas = LINHAS[bucket]
    rows = monthly_apuracao(grade, linhas)
    n = len(grade)
    mes = [rows[k * n + n - 1] for k in range(len(linhas))]
    inicial = rows[n - 2]["carry"] if n > 1 else 0.0
    return {"gross": mes[0]["gross"], "isento": mes[0]["isento"],
            "pnl": sum(r["pnl"] for r in mes),
            "base": sum(r["base"] for r in mes), "ir": sum(r["ir"] for r in mes),
            "ativo": any(r["ativo"] for r in mes), "carry_inicial": inicial,
            "carry_after": mes[0]["carry"],
            "carry_used": max(0.0, inicial - mes[0]["carry"])}


def _decisao(status: Status, rationale: str, antes: dict, depois: dict,
             target_loss: float) -> dict:
    """The one shape every verdict has (tlh_plan.build_result reads it)."""
    return {"status": status, "rationale": rationale,
            "gross_after": antes["gross"], "pnl_after": antes["pnl"],
            "target_loss": target_loss, "carry_used": depois["carry_used"],
            "carry_after": depois["carry_after"],
            "final_gross": depois["gross"], "final_pnl": depois["pnl"],
            "ir": depois["ir"]}


def _por_que_nada(m: dict) -> str:
    if m["carry_used"] > 0.005:
        return (f"Prejuízo acumulado (R$ {m['carry_inicial']:,.2f}) já cobre o ganho "
                f"do mês (R$ {m['carry_used']:,.2f}). Carry consumido absorve tudo.")
    if m["isento"]:
        return (f"Vendas brutas de ações no mês ({m['gross']:,.2f}) ≤ R$ 20.000 → "
                f"mês isento. Lucro vai para Rendimentos Isentos código 20 no IRPF.")
    return "Mês já tem PnL ≤ 0 — nada a tributar (prejuízo vai pro carry)."


def decidir(bucket: str, grade: pd.DataFrame, vendas: list[dict],
            candidatos, harvest) -> tuple[dict | None, list[dict]]:
    """(verdict, harvest plan) for one bucket, or (None, []) with nothing to plan.

    `grade` is the whole history with the current month last; `vendas` the
    proposed sells; `candidatos()` the bucket's losers, worst first; `harvest`
    the greedy that picks from them (tlh_plan.harvest).
    """
    proprias = [v for v in vendas if DESTINO.get(v["cls"], (None,))[0] == bucket]
    antes = apurar_mes(com_vendas(grade, proprias), bucket)
    if not proprias and not antes["ativo"]:
        return None, []
    if antes["ir"] <= 0:
        return _decisao(Status.NENHUM, _por_que_nada(antes), antes, antes, 0.0), []
    residual = antes["base"]
    losers = candidatos()
    if losers.empty:
        return _decisao(Status.SEM_CANDIDATOS,
                        f"Sem posições em prejuízo no bucket {bucket} que cubram "
                        f"R$ {residual:,.2f}. IR estimado: R$ {antes['ir']:,.2f}.",
                        antes, antes, -residual), []
    plan = harvest(-residual, losers)
    colhido = [{"cls": p.get("cls", bucket), "sell_value": p["price"] * p["qty"],
                "pnl": p["realized_loss"]} for p in plan]
    depois = apurar_mes(com_vendas(grade, proprias + colhido), bucket)
    harvested = sum(p["realized_loss"] for p in plan)
    return _decisao(Status.RECOMENDADO,
                    f"Ganho residual após carry: R$ {residual:,.2f}. Plano realiza "
                    f"R$ {harvested:+,.2f} em perdas, levando PnL final a "
                    f"R$ {depois['pnl']:+,.2f}.", antes, depois, -residual), plan
