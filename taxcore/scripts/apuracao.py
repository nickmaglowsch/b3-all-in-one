"""The Renda Variável apuração rules — isenção, alíquotas, compensação de prejuízo.

Split out of `irpf_2026.py` so mutation testing has a target it can finish: mutmut
generates every mutant of a module into one file, and at 1100 lines the report script
produced a 42 MB module that never finished importing. This module does it in seconds
— and these are the lines where a surviving mutant is a real tax bug.

`irpf_2026.py` re-exports all four names, so `from scripts.irpf_2026 import
monthly_apuracao` still works.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# The three lines the Renda Variável ficha actually apurates, in render order.
# ETF_RF is deliberately absent: renda fixa is tributada na fonte, so it gets a
# table in the report but no apuração, no IR to pay, and no DARF.
#
# CRIPTO and EXTERIOR are in REGRAS but NOT here, on purpose: the report is built
# from B3 extracts, which carry neither, so rendering them would add twelve empty
# "—" months per class to every user's declaration. They become render classes
# when there is a parser feeding them.
CLASSES_APURACAO = ("AÇÃO", "FII", "BDR_ETF")


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
    does, per class. Ganho de capital does not: a crypto loss is simply lost,
    which is the single most surprising rule here and the one most likely to be
    "fixed" into a bug by someone assuming every class works like ações.

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
    "AÇÃO": Regra(("AÇÃO",), 0.15, 20_000.0, True, "6015", "mensal"),
    "FII": Regra(("FII",), 0.20, None, True, "6015", "mensal"),
    # One summed line, 15%, no isenção and no carry bucket — which is what this
    # report has always done for it.
    "BDR_ETF": Regra(("BDR", "ETF_RV"), 0.15, None, False, "6015", "mensal"),
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


def monthly_apuracao(monthly_df: pd.DataFrame, classes=CLASSES_APURACAO) -> list[dict]:
    """Per-month, per-class apuração: compensação, base, IR and running carry.

    One row per (class, month) for all twelve months — a month with no trades is
    reported as ``ativo=False`` rather than dropped, because the report renders a
    "—" line for it and a caller must not have to invent one.

    The rules live here, once, because the three render loops below and the
    platform's DARF ledger read the same numbers and must not drift:

    - **AÇÕES**: isentas when the month's gross sales are ``<= R$ 20.000``
      (inclusive). Best of both worlds — an isento *gain* is tax-free and does
      **not** consume the accumulated loss, while an isento *loss* still feeds
      it. Above the limit: 15% on the gain net of carry.
    - **FIIs**: 20% from the first real, no isenção, own carry bucket. FII losses
      never offset ação gains.
    - **BDR / ETF de renda variável**: one summed line at 15%, no isenção and no
      carry bucket — which is what this report has always done for it.
    - **CRIPTO** (only when asked for by name — see CLASSES_APURACAO): isento up
      to R$35.000 of gross monthly alienações, progressive ganho de capital rate
      above it, and **no carry**: a crypto loss is not compensable, so an isento
      loss feeds nothing and a taxed month gets no compensação.

    Money is left unrounded: the caller formats with ``:,.2f``, and rounding each
    row before summing would shift the yearly totals by cents.
    """
    rows: list[dict] = []
    for cls in classes:
        regra = REGRAS[cls]
        carry = 0.0
        for _, r in monthly_df.iterrows():
            gross = sum(float(r[f"{f}_gross"]) for f in regra.fontes)
            pnl = sum(float(r[f"{f}_pnl"]) for f in regra.fontes)
            isento = regra.isencao is not None and gross <= regra.isencao
            compensacao = ganho_isento = base = 0.0
            if isento:
                if pnl > 0:
                    ganho_isento = pnl
                elif pnl < 0 and regra.carry:
                    carry += -pnl
            elif pnl > 0:
                if regra.carry:
                    compensacao = min(carry, pnl)
                    carry -= compensacao
                base = pnl - compensacao
            elif pnl < 0 and regra.carry:
                carry += -pnl
            aliquota = gcap_aliquota(base) if regra.progressiva else regra.aliquota
            rows.append({
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
                "carry": carry,
                "darf": regra.darf,
            })
    return rows


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
