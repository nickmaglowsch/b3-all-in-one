"""The DARF ledger's arithmetic: one DARF 6015 per apuração month.

Pure functions over what taxcore's ``irpf_2026 --json`` returns and the payments
the tenant recorded — routes.py does the I/O. Every tax number here is taxcore's:
`darf` rows already carry the sum of every Renda Variável line for the month, the
IRRF deducted and the R$ 10,00 rollover (Lei 9.430/1996, art. 68), so the ledger
never bills a figure the IRPF report does not print.

One row per month, not per class: Ações, FII, BDR/ETF and day trade all go on the
one DARF código 6015. Payments recorded per class before this existed are summed
into their month, so nothing a tenant typed is lost.
"""
from __future__ import annotations

import calendar
import datetime

# The apuração lines taxcore returns (irpf_2026.CLASSES_APURACAO), plus TOTAL —
# the one-per-month payment the ledger records now. The per-class names stay
# accepted so a payment recorded before still round-trips.
DARF_CLASSES = ("AÇÃO", "FII", "BDR_ETF", "DAYTRADE", "TOTAL")
# Ganhos líquidos em renda variável, pessoa física. One code for every class:
# the rate differs, the DARF's código does not.
DARF_CODIGO = "6015"


def vencimento(apuracao: datetime.date) -> datetime.date:
    """Último dia útil do mês seguinte — when that month's DARF is due.

    ponytail: weekdays only, no feriado calendar. A national holiday landing on
    the last business day pulls the real deadline one day earlier, so this can
    only ever call something "atrasado" later than the Receita would — never
    earlier, which is the direction that would cry wolf.
    """
    if apuracao.month == 12:
        year, month = apuracao.year + 1, 1
    else:
        year, month = apuracao.year, apuracao.month + 1
    due = datetime.date(year, month, calendar.monthrange(year, month)[1])
    while due.weekday() >= 5:  # Saturday, Sunday
        due -= datetime.timedelta(days=1)
    return due


def pagamento_do_mes(pagos: list[dict]) -> dict | None:
    """The month's recorded payment; legacy per-class rows summed into one."""
    if len(pagos) <= 1:
        return pagos[0] if pagos else None
    datas = [p["data_pagamento"] for p in pagos if p["data_pagamento"]]
    return {**pagos[0], "classe": "TOTAL",
            "valor_principal": round(sum(p["valor_principal"] for p in pagos), 2),
            "data_pagamento": max(datas) if datas else None,
            "obs": "; ".join(p["obs"] for p in pagos if p["obs"]) or None}


def _status(pago, darf: float, acumulado: float, venc: datetime.date,
            today: datetime.date) -> str:
    if pago:
        return "pago"
    if darf <= 0:
        # Nothing to pay this month: below R$ 10 and handed to the next one,
        # or covered by the IRRF the broker already withheld.
        return "acumulado" if acumulado > 0 else "compensado"
    return "atrasado" if today > venc else "pendente"


def livro(year: int, payload: dict, pagamentos: dict, today: datetime.date) -> dict:
    """The ledger response. `pagamentos` maps the apuração month (a date) to the
    payment dicts recorded for it."""
    linhas = {r["month"]: [] for r in payload.get("apuracao", [])}
    for r in payload.get("apuracao", []):
        if r["ir"] > 0:
            linhas[r["month"]].append({k: r[k] for k in (
                "classe", "gross", "pnl", "compensacao", "base", "aliquota", "ir")})
    meses = []
    for d in payload.get("darf", []):
        apuracao = datetime.date(year, d["month"], 1)
        pago = pagamento_do_mes(pagamentos.get(apuracao, []))
        if d["imposto"] <= 0 and d["acumulado_anterior"] <= 0 and pago is None:
            continue
        venc = vencimento(apuracao)
        meses.append({
            "apuracao": apuracao.strftime("%Y-%m"), "classe": "TOTAL",
            "codigo": DARF_CODIGO, "classes": linhas.get(d["month"], []),
            "imposto": round(d["imposto"], 2),
            "irrf_deduzido": round(d["irrf_deduzido"], 2),
            "acumulado_anterior": round(d["acumulado_anterior"], 2),
            "acumulado": round(d["acumulado"], 2),
            # What goes on the DARF: zero when the month rolled forward.
            "ir_devido": round(d["darf"], 2),
            "vencimento": venc.isoformat(), "pagamento": pago,
            # "atrasado" is the alert: a DARF due, past its date, nothing recorded.
            "status": _status(pago, d["darf"], d["acumulado"], venc, today),
        })
    return {
        "year": year, "codigo": DARF_CODIGO, "meses": meses,
        "total_devido": round(sum(m["ir_devido"] for m in meses), 2),
        "total_pago": round(sum(m["pagamento"]["valor_principal"]
                                for m in meses if m["pagamento"]), 2),
        "atrasados": sum(1 for m in meses if m["status"] == "atrasado"),
    }
