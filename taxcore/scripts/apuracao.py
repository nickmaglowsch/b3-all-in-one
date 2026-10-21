"""The Renda Variável apuração rules — isenção, alíquotas, compensação de prejuízo.

Split out of `irpf_2026.py` so mutation testing has a target it can finish: mutmut
generates every mutant of a module into one file, and at 1100 lines the report script
produced a 42 MB module that never finished importing. This module does it in seconds
— and these are the lines where a surviving mutant is a real tax bug.

`irpf_2026.py` re-exports `CLASSES_APURACAO` and `monthly_apuracao`, so
`from scripts.irpf_2026 import monthly_apuracao` still works.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# The lines the Renda Variável fichas actually apurate, in render order.
# ETF_RF is deliberately absent: renda fixa is tributada na fonte, so it gets a
# table in the report but no apuração, no IR to pay, and no DARF.
#
# CRIPTO and EXTERIOR are in REGRAS but NOT here, on purpose: the report is built
# from B3 extracts, which carry neither, so rendering them would add twelve empty
# "—" months per class to every user's declaration. They become render classes
# when there is a parser feeding them.
CLASSES_APURACAO = ("AÇÃO", "FII", "BDR_ETF", "DAYTRADE")


@dataclass(frozen=True)
class Regra:
    """How one asset class is apurada.

    The four fields that vary are exactly the four ways Brazilian law treats
    these classes differently, and every one of them bites:

    `fontes` — which `{prefix}_gross` / `{prefix}_pnl` columns sum into this
    line. BDR and ETF de renda variável share one.

    `aliquota` / `progressiva` — a flat rate, or a flag saying the rate comes
    from the ganho de capital table instead, because crypto's rate depends on the
    size of the gain rather than on the class.

    `isencao` — the threshold on the month's **gross sales**, not on the gain.
    R$20k for ações, R$35k for cripto, ``None`` (not zero) for the rest: a class
    with no isenção and a class whose isenção happens to be zero would behave
    differently on a month with no sales at all, and only one of them is real.

    `carry` — whether the class has a loss-carry bucket at all. Renda variável
    does. Ganho de capital does not: a crypto loss is simply lost,
    which is the single most surprising rule here and the one most likely to be
    "fixed" into a bug by someone assuming every class works like ações.

    `grupo` — the loss bucket a class shares with another line; ``None`` is a
    bucket of its own. Ações, BDRs and ETFs de renda variável are all *operações
    comuns* (IN RFB 1.585/2015, art. 64): one bucket, two lines only because the
    isenção is ações-only. FII (Lei 8.668/93, art. 18) and day trade (IN 1.585,
    art. 65) each keep their own.

    `periodo` — "mensal" for everything under the renda variável / ganho de
    capital regimes; "anual" for aplicações financeiras no exterior, where Lei
    14.754/2023 replaced monthly DARFs with one annual apuração settled in the
    ajuste. That is a different shape, not a different rate — see annual_apuracao.
    """
    fontes: tuple
    aliquota: float
    isencao: "float | None"
    carry: bool
    darf: str
    periodo: str
    progressiva: bool = False
    grupo: "str | None" = None


# Ganho de capital, tabela progressiva (Lei 13.259/2016, art. 21 da Lei 8.981).
# Applies to cripto: the rate is chosen by the size of the gain, not by the class.
GCAP_FAIXAS = ((5_000_000.0, 0.15), (10_000_000.0, 0.175), (30_000_000.0, 0.20))
GCAP_TETO = 0.225


def gcap_aliquota(ganho: float) -> float:
    """The progressive ganho de capital rate for a gain of `ganho`.

    Bands are inclusive at the top: a gain of exactly R$5.000.000 pays 15%.
    """
    for teto, taxa in GCAP_FAIXAS:
        if ganho <= teto:
            return taxa
    return GCAP_TETO


REGRAS = {
    "AÇÃO": Regra(("AÇÃO",), 0.15, 20_000.0, True, "6015", "mensal", grupo="COMUNS"),
    "FII": Regra(("FII",), 0.20, None, True, "6015", "mensal"),
    # BDR and ETF de renda variável: 15%, no isenção (Soluções de Consulta Cosit
    # 39/2022 and 145/2021), and the same operações comuns bucket as ações — an
    # ETF loss shelters an ação gain and vice versa.
    "BDR_ETF": Regra(("BDR", "ETF_RV"), 0.15, None, True, "6015", "mensal",
                     grupo="COMUNS"),
    # Day trade (same ticker bought and sold the same day at the same broker —
    # Lei 9.959/2000, art. 8º, § 1º): 20%, no isenção, a bucket of its own in
    # both directions (IN 1.585, arts. 64 and 65).
    "DAYTRADE": Regra(("DT",), 0.20, None, True, "6015", "mensal"),
    # Cripto: ganho de capital. Monthly apuração, DARF 4600, isenção on R$35k of
    # gross monthly alienações — and no loss compensation, because ganho de
    # capital has none.
    #
    # As of 2026 these are still the rules. MP 1.303/2025 would have made it a
    # flat 17,5% and killed the isenção; it lapsed in October 2025 without a
    # Congressional vote. This belongs to the yearly IRPF rule review.
    "CRIPTO": Regra(("CRIPTO",), 0.15, 35_000.0, False, "4600", "mensal",
                    progressiva=True),
    # Aplicações financeiras no exterior (Lei 14.754/2023): 15% flat, apurado
    # ANUALMENTE and paid with the ajuste — there is no monthly DARF at all.
    # Losses offset gains across foreign financial applications within the year,
    # so it has a carry bucket, but one that lives on the annual grid.
    "EXTERIOR": Regra(("EXTERIOR",), 0.15, None, True, "-", "anual"),
}

# Back-compat: irpf_2026 and tlh_plan read these names.
ALIQUOTA = {cls: r.aliquota for cls, r in REGRAS.items() if r.periodo == "mensal"}
ISENCAO_LIMIT = REGRAS["AÇÃO"].isencao


def _linha(regra: Regra, r) -> tuple:
    """(gross, pnl, isento, ganho_isento) for one class in one month."""
    gross = sum(float(r[f"{f}_gross"]) for f in regra.fontes)
    pnl = sum(float(r[f"{f}_pnl"]) for f in regra.fontes)
    isento = regra.isencao is not None and gross <= regra.isencao
    ganho_isento = max(0.0, pnl) if isento else 0.0
    return gross, pnl, isento, ganho_isento


def monthly_apuracao(monthly_df: pd.DataFrame, classes=CLASSES_APURACAO) -> list[dict]:
    """Per-month, per-class apuração: compensação, base, IR and running carry.

    One row per (class, month) for every month in `monthly_df` — a month with no
    trades is reported as ``ativo=False`` rather than dropped, because the report
    renders a "—" line for it and a caller must not have to invent one. Pass the
    *whole* history, not one year: the carry has no time limit (IN RFB
    1.585/2015, art. 64), so a year apurated on its own forgets every earlier
    prejuízo. A ``year`` column, when present, is copied onto each row.

    The rules live here, once, because the render loops and the platform's DARF
    ledger read the same numbers and must not drift:

    - **AÇÕES**: isentas when the month's gross sales are ``<= R$ 20.000``
      (inclusive). Best of both worlds — an isento *gain* is tax-free and does
      **not** consume the accumulated loss, while an isento *loss* still feeds
      it. Above the limit: 15% on the gain net of carry.
    - **BDR / ETF de renda variável**: 15%, no isenção, and the *same* loss
      bucket as ações (`grupo`). Within a month every loss of the bucket is
      counted before any gain spends it — the month's net result.
    - **FIIs**: 20% from the first real, no isenção, own carry bucket.
    - **DAYTRADE**: 20%, no isenção, own carry bucket.
    - **CRIPTO** (only when asked for by name — see CLASSES_APURACAO): isento up
      to R$35.000 of gross monthly alienações, progressive ganho de capital rate
      above it, and **no carry**: a crypto loss is not compensable, so an isento
      loss feeds nothing and a taxed month gets no compensação.

    Money is left unrounded: the caller formats with ``:,.2f``, and rounding each
    row before summing would shift the yearly totals by cents.
    """
    regras = {cls: REGRAS[cls] for cls in classes}
    rows: dict = {cls: [] for cls in classes}
    carry: dict = {}
    for _, r in monthly_df.iterrows():
        linhas = {cls: _linha(regra, r) for cls, regra in regras.items()}
        # Losses first, across the whole bucket, so a loss on one line of a shared
        # bucket shelters a gain on the other line of the same month.
        for cls, regra in regras.items():
            _, pnl, _, ganho_isento = linhas[cls]
            perda = max(0.0, ganho_isento - pnl) if regra.carry else 0.0
            pool = regra.grupo or cls
            carry[pool] = carry.get(pool, 0.0) + perda
        mes = []
        for cls, regra in regras.items():
            gross, pnl, isento, ganho_isento = linhas[cls]
            pool = regra.grupo or cls
            ganho = max(0.0, pnl - ganho_isento)
            compensacao = min(carry[pool], ganho)
            carry[pool] -= compensacao
            base = ganho - compensacao
            aliquota = gcap_aliquota(base) if regra.progressiva else regra.aliquota
            mes.append((pool, {
                "year": int(r["year"]) if "year" in r.index else None,
                "month": int(r["month"]),
                "classe": cls,
                # "traded and owed nothing" is not "did not trade": the first
                # prints real numbers, the second prints "—", and neither may
                # raise a DARF alert.
                "ativo": bool(gross or pnl),
                "gross": gross,
                "pnl": pnl,
                "isento": isento,
                "ganho_isento": ganho_isento,
                "compensacao": compensacao,
                "base": base,
                "aliquota": aliquota,
                "ir": base * aliquota,
                "darf": regra.darf,
            }))
        # The bucket as it closes the month, on every line that shares it.
        for pool, row in mes:
            row["carry"] = carry[pool]
            rows[row["classe"]].append(row)
    return [row for cls in classes for row in rows[cls]]


# The monthly DARF 6015 is one payment for every renda variável line, but the IRRF
# withheld on each line is deducted inside its own ficha of the IRPF program:
# operações comuns and day trade share one, FIIs have another.
FICHAS_6015 = {"AÇÃO": "COMUNS", "BDR_ETF": "COMUNS", "DAYTRADE": "COMUNS",
               "FII": "FII"}
# Lei 9.430/1996, art. 68: no DARF below R$ 10,00 — the amount is added to the
# following months until the total reaches it, and is paid by that month's due date.
DARF_MINIMO = 10.0


def darf_mensal(rows: list[dict], irrf: "dict | None" = None) -> list[dict]:
    """One DARF 6015 per month, from the `monthly_apuracao` rows of the 6015 lines.

    `irrf` maps ``(year, month)`` to ``{classe: IRRF withheld}`` — the 0,005%
    "dedo-duro" on sales and the 1% on day-trade gains. It is deducted from the IR
    of its own ficha in the month; what is left over is deducted in the following
    months of the **same year** and after that belongs to the declaração de ajuste
    (Lei 11.033/2004, art. 2º, § 7º), so a saldo never crosses into January.

    A month whose total is below R$ 10,00 pays nothing and hands the amount on to
    the next month (Lei 9.430/96, art. 68, § 1º) — across the year boundary too.
    """
    irrf = irrf or {}
    meses: dict = {}
    for r in rows:
        mes = meses.setdefault((r["year"], r["month"]), {})
        ficha = FICHAS_6015[r["classe"]]
        mes[ficha] = mes.get(ficha, 0.0) + r["ir"]
    out: list[dict] = []
    saldos: dict = {}  # year → {ficha: unused IRRF}: a saldo lives inside its year
    acumulado = 0.0
    for (ano, mes_n), devido in sorted(meses.items()):
        saldo = saldos.setdefault(ano, {})
        for classe, valor in irrf.get((ano, mes_n), {}).items():
            ficha = FICHAS_6015[classe]
            saldo[ficha] = saldo.get(ficha, 0.0) + valor
        deduzido = 0.0
        for ficha, ir in devido.items():
            usado = min(ir, saldo.get(ficha, 0.0))
            saldo[ficha] = saldo.get(ficha, 0.0) - usado
            deduzido += usado
        imposto = sum(devido.values())
        total = imposto - deduzido + acumulado
        pagar = total >= DARF_MINIMO
        out.append({
            "year": ano, "month": mes_n,
            "imposto": imposto,
            "irrf_deduzido": deduzido,
            "acumulado_anterior": acumulado,
            "darf": total if pagar else 0.0,
            "acumulado": 0.0 if pagar else total,
            # IRRF not yet used, per ficha; what is left after December is
            # declared in the ajuste anual.
            "irrf_saldo": dict(saldo),
        })
        acumulado = 0.0 if pagar else total
    return out


def annual_apuracao(pnl_por_ativo, classe: str = "EXTERIOR") -> dict:
    """One year of aplicações financeiras no exterior, in one apuração.

    Lei 14.754/2023 changed the shape, not just the rate. Before it, a sale
    abroad was a ganho de capital with its own monthly DARF and a US$5k isenção;
    now the year's foreign financial applications are netted, taxed at 15%, and
    paid with the ajuste anual. There is no monthly DARF to raise and no monthly
    isenção to check, which is why this cannot be a row in the month grid above.

    Losses net against gains **within the year and across assets** — that is the
    compensation the law introduced in exchange for ending the deferral — and a
    net loss for the year produces no tax and carries nothing forward here.

    Args:
        pnl_por_ativo: {name: result in BRL}, positive for a gain.

    Returns a dict with the netted figures and the IR owed.
    """
    regra = REGRAS[classe]
    # max(), not a filtered sum: `if v > 0` and `if v >= 0` produce identical
    # totals (a zero adds nothing), so the comparison would be a mutant no test
    # could ever kill — an untestable line in a tax rule is a line nobody can
    # trust.
    ganhos = sum(max(0.0, v) for v in pnl_por_ativo.values())
    perdas = sum(max(0.0, -v) for v in pnl_por_ativo.values())
    base = max(0.0, ganhos - perdas)
    aliquota = regra.aliquota
    return {
        "classe": classe,
        "ativo": bool(pnl_por_ativo),
        "ganhos": float(ganhos),
        "perdas": float(perdas),
        # Netted, not summed: a year that lost more than it made owes nothing,
        # and the excess loss does not become a carry — the law nets inside the
        # year, and this function is one year.
        "base": float(base),
        "aliquota": aliquota,
        "ir": float(base * aliquota),
        "periodo": regra.periodo,
        "darf": regra.darf,
    }
