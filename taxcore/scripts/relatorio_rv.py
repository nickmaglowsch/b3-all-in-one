"""Section 3 of the IRPF report — Renda Variável — and the --json payload.

Rendering only: every number comes from `realizacao.apurar`, which runs the
apuração over the whole history. Split out of irpf_2026.py, whose `main` was
already the most complex function in the package.
"""
from __future__ import annotations

from scripts.ativos_b3 import fii_presumido

MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
         "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def avisos(rv: dict, tl) -> dict:
    """What the apuração could not settle on its own, for the report and the API."""
    r = rv["realized"]
    desc = r[r["descoberto"] > 0]
    return {
        "descoberto": [{"date": d["date"].date().isoformat(), "ticker": d["ticker"],
                        "qty": float(d["descoberto"]),
                        "valor": float(d["descoberto"] * d["sell_price"])}
                       for _, d in desc.iterrows()],
        "fii_presumido": sorted({t for t in tl["ticker"].astype(str) if fii_presumido(t)}),
        "carry_inicial": rv["carry_inicial"],
    }


def payload_json(ano: int, rv: dict, tl) -> dict:
    return {"year": ano, "apuracao": rv["apuracao"], "darf": rv["darf"],
            "avisos": avisos(rv, tl)}


def _linha_mes(w, r, isento_note: bool = False) -> float:
    """One month of a class table; returns the IR it adds to the year's total."""
    m, gross, pnl, carry = r["month"], r["gross"], r["pnl"], r["carry"]
    if not r["ativo"]:
        w(f"| {MESES[m-1]} | — | — | — | — | R$ {carry:,.2f} |")
        return 0.0
    if isento_note and r["isento"]:
        # Gains are tax-free (código 20) and DON'T consume the loss carry;
        # losses still accumulate (best of both worlds — see monthly_apuracao).
        note = ("**ISENTO** (≤ R$ 20k → código 20)" if pnl > 0 else
                "**ISENTO** (prejuízo somado ao acumulado)" if pnl < 0 else
                "**ISENTO** (≤ R$ 20k)")
        w(f"| {MESES[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | — | {note} | R$ {carry:,.2f} |")
        return 0.0
    if pnl > 0:
        w(f"| {MESES[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | R$ {r['compensacao']:,.2f} "
          f"| R$ {r['ir']:,.2f} | R$ {carry:,.2f} |")
        return r["ir"]
    zero = "R$ 0,00 (prejuízo)" if isento_note else "R$ 0,00"
    w(f"| {MESES[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | — | {zero} | R$ {carry:,.2f} |")
    return 0.0


def _tabela(w, rows, cabecalho: str, isento_note: bool = False) -> float:
    w(cabecalho)
    w("|---|---|---|---|---|---|")
    return sum(_linha_mes(w, r, isento_note) for r in rows)


def _intro(w, ano: int) -> None:
    w(f"\n## 3) Renda Variável — Operações Comuns + FII ({ano})\n")
    w("Ficha **Renda Variável → Operações Comuns/Day-Trade**. Preencha por mês:\n")
    w("- **AÇÕES (operações comuns)**: isenção R$ 20.000/mês em vendas brutas. Se "
      "o gross do mês for ≤ R$ 20k → isento (não preencha ganho líquido). Se > R$ 20k → "
      "15% sobre o resultado líquido positivo, compensando prejuízos acumulados.\n")
    w("- **FIIs**: 20% sobre ganho líquido, sem isenção. Prejuízos de FII só compensam "
      "ganhos futuros de FII.\n")
    w("- **BDR / ETF Renda Variável**: 15% sobre ganho líquido, sem isenção. São "
      "**operações comuns**, como as ações: o prejuízo de um compensa o ganho do outro "
      "(IN RFB 1.585/2015, art. 64).\n")
    w("- **Day trade** (compra e venda do mesmo ativo no mesmo dia, na mesma corretora): "
      "20%, sem isenção; prejuízo de day trade só compensa ganho de day trade.\n")


def _avisos(w, av: dict) -> None:
    carry = {k: v for k, v in av["carry_inicial"].items() if v > 0.005}
    if carry:
        w("\n**Prejuízo acumulado trazido de anos anteriores** (apurado sobre todo o "
          "histórico importado): "
          + " · ".join(f"{k} R$ {v:,.2f}" for k, v in carry.items())
          + ". Só é compensável se constou das declarações anteriores — confira.\n")
    if av["descoberto"]:
        w("\n⚠ **Vendas acima da posição registrada** — o valor total entra nas vendas "
          "brutas do mês (é ele que decide a isenção), mas o resultado da parte "
          "descoberta **não foi apurado**: falta histórico (compra anterior ao extrato, "
          "subscrição, transferência). Complete o histórico antes de declarar.\n")
        w("| Data | Ticker | Qtde sem custo | Valor da venda |")
        w("|---|---|---|---|")
        for d in av["descoberto"]:
            w(f"| {d['date']} | {d['ticker']} | {d['qty']:g} | R$ {d['valor']:,.2f} |")
    if av["fii_presumido"]:
        w(f"\n⚠ **Classificação presumida**: {', '.join(av['fii_presumido'])} terminam em "
          f"11 e não estão nas listas de ETFs, units ou FIIs conhecidos "
          f"(`scripts/ativos_b3.py`) — foram tratados como FII (20%, prejuízo só contra "
          f"FII). Se algum for ETF ou unit, a alíquota e a compensação mudam.\n")


def _acoes(w, ano, rows, isentos_total) -> None:
    w(f"\n### Tabela mensal {ano} — AÇÕES (operações comuns)")
    w("\n> **Atenção — o melhor dos dois mundos:** em meses com vendas brutas ≤ R$ 20.000, "
      "o **ganho é isento** (vai para Rendimentos Isentos código 20, ver seção 2.1) e "
      "**não consome o prejuízo acumulado** — o saldo de compensação fica intacto para "
      "meses tributáveis futuros. Já o **prejuízo** num mês isento **continua sendo "
      "adicionado ao prejuízo acumulado** e pode ser compensado em meses tributáveis "
      "futuros normalmente. O prejuízo acumulado é o das operações comuns — ações, BDRs "
      "e ETFs juntos.\n")
    total = _tabela(w, rows, "\n| Mês | Vendas brutas | Resultado líquido | Compensação "
                             "| IR a pagar (15%) | Prejuízo acumulado |", isento_note=True)
    w(f"\n**Total IR sobre AÇÕES em {ano}**: R$ {total:,.2f}")
    w(f"\n**Saldo prejuízo operações comuns (ações, BDRs, ETFs) a compensar p/ {ano+1}**: "
      f"R$ {rows[-1]['carry']:,.2f}")
    w(f"\n**Ganhos isentos AÇÕES em {ano} (vendas mensais ≤ R$ 20k)**: "
      f"R$ {isentos_total:,.2f} → declarar em Rendimentos Isentos código 20.")


def _outras(w, ano, by_class, grade) -> None:
    total = _tabela(w, by_class["FII"], f"\n### Tabela mensal {ano} — FIIs\n\n| Mês | Vendas "
                    "brutas | Resultado líquido | Compensação | IR a pagar (20%) | Prejuízo acumulado |")
    w(f"\n**Total IR sobre FIIs em {ano}**: R$ {total:,.2f}")
    w(f"\n**Saldo prejuízo FIIs a compensar p/ {ano+1}**: R$ {by_class['FII'][-1]['carry']:,.2f}")
    if grade[["BDR_gross", "ETF_RV_gross"]].sum().sum() > 0:
        total = _tabela(w, by_class["BDR_ETF"], f"\n### Tabela mensal {ano} — BDRs e ETFs de "
                        "Renda Variável (15% sem isenção, compensa com ações)\n\n| Mês | Vendas "
                        "brutas | Resultado líquido | Compensação | IR (15%) | Prejuízo acumulado |")
        w(f"\n**Total IR sobre BDRs/ETFs em {ano}**: R$ {total:,.2f}")
    if any(r["ativo"] for r in by_class["DAYTRADE"]):
        total = _tabela(w, by_class["DAYTRADE"], f"\n### Tabela mensal {ano} — Day trade (20%, "
                        "sem isenção)\n\n| Mês | Vendas | Resultado líquido | Compensação "
                        "| IR (20%) | Prejuízo acumulado |")
        w(f"\n**Total IR sobre day trade em {ano}**: R$ {total:,.2f}")


def _etf_rf(w, ano, grade) -> None:
    if grade["ETF_RF_gross"].sum() <= 0:
        return
    w(f"\n### Tabela mensal {ano} — ETFs de Renda Fixa (LFTB11)")
    w("\n| Mês | Vendas brutas | Resultado líquido |")
    w("|---|---|---|")
    for _, r in grade.iterrows():
        m = int(r["month"])
        if r["ETF_RF_gross"] == 0 and r["ETF_RF_pnl"] == 0:
            w(f"| {MESES[m-1]} | — | — |")
            continue
        w(f"| {MESES[m-1]} | R$ {r['ETF_RF_gross']:,.2f} | R$ {r['ETF_RF_pnl']:+,.2f} |")
    w("\n*ETFs de Renda Fixa (LFTB11 etc.) são tributados pela tabela regressiva da "
      "renda fixa (22.5% se ≤180d, 20% 181-360d, 17.5% 361-720d, 15% > 720d) — "
      "NÃO entram em \"Renda Variável\". Use os dados do Informe do broker para "
      "preencher o ficha \"Rendimentos sujeitos a tributação exclusiva\".*")


def _darf(w, ano, darfs) -> None:
    w(f"\n### DARF mensal — código 6015 ({ano})")
    w("\nUm único DARF por mês soma todas as linhas acima. Abaixo de R$ 10,00 não se "
      "emite DARF: o valor passa para o mês seguinte até o total chegar a R$ 10,00 "
      "(Lei 9.430/1996, art. 68). O IRRF (0,005% das vendas, 1% do ganho em day trade) "
      "é **estimado** a partir das vendas — confira nas notas de corretagem.\n")
    linhas = [d for d in darfs if d["imposto"] > 0 or d["acumulado_anterior"] > 0]
    if not linhas:
        w(f"Nenhum DARF devido em {ano}.")
    else:
        w("| Mês | IR apurado | IRRF deduzido | Acumulado (< R$ 10) | DARF a pagar |")
        w("|---|---|---|---|---|")
    for d in linhas:
        pagar = f"R$ {d['darf']:,.2f}" if d["darf"] else f"— (R$ {d['acumulado']:,.2f} → mês seguinte)"
        w(f"| {MESES[d['month']-1]} | R$ {d['imposto']:,.2f} | R$ {d['irrf_deduzido']:,.2f} "
          f"| R$ {d['acumulado_anterior']:,.2f} | {pagar} |")
    saldo = sum(darfs[-1]["irrf_saldo"].values()) if darfs else 0.0
    if saldo > 0.005:
        w(f"\n**IRRF não compensado em {ano}**: R$ {saldo:,.2f} → declarar como imposto "
          f"pago/retido no ajuste anual.")


def render_secao3(w, ano: int, rv: dict, tl, by_class: dict, isentos_total: float) -> None:
    _intro(w, ano)
    _avisos(w, avisos(rv, tl))
    _acoes(w, ano, by_class["AÇÃO"], isentos_total)
    _outras(w, ano, by_class, rv["grade"])
    _etf_rf(w, ano, rv["grade"])
    _darf(w, ano, rv["darf"])
