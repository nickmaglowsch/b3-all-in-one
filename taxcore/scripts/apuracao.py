"""The Renda Variável apuração rules — isenção, alíquotas, compensação de prejuízo.

Split out of `irpf_2026.py` so mutation testing has a target it can finish: mutmut
generates every mutant of a module into one file, and at 1100 lines the report script
produced a 42 MB module that never finished importing. Seventy lines does it in
seconds — and these seventy are the ones where a surviving mutant is a real tax bug.

`irpf_2026.py` re-exports all four names, so `from scripts.irpf_2026 import
monthly_apuracao` still works.
"""
from __future__ import annotations

import pandas as pd

# The three lines the Renda Variável ficha actually apurates, in render order.
# ETF_RF is deliberately absent: renda fixa is tributada na fonte, so it gets a
# table in the report but no apuração, no IR to pay, and no DARF.
CLASSES_APURACAO = ("AÇÃO", "FII", "BDR_ETF")
ALIQUOTA = {"AÇÃO": 0.15, "FII": 0.20, "BDR_ETF": 0.15}
ISENCAO_LIMIT = 20_000.0


def monthly_apuracao(monthly_df: pd.DataFrame) -> list[dict]:
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

    Money is left unrounded: the caller formats with ``:,.2f``, and rounding each
    row before summing would shift the yearly totals by cents.
    """
    rows: list[dict] = []
    for cls in CLASSES_APURACAO:
        carry = 0.0
        for _, r in monthly_df.iterrows():
            if cls == "BDR_ETF":
                gross = float(r["BDR_gross"] + r["ETF_RV_gross"])
                pnl = float(r["BDR_pnl"] + r["ETF_RV_pnl"])
            else:
                gross = float(r[f"{cls}_gross"])
                pnl = float(r[f"{cls}_pnl"])
            isento = cls == "AÇÃO" and gross <= ISENCAO_LIMIT
            has_carry_bucket = cls != "BDR_ETF"
            compensacao = ganho_isento = base = 0.0
            if isento:
                if pnl > 0:
                    ganho_isento = pnl
                elif pnl < 0:
                    carry += -pnl
            elif pnl > 0:
                if has_carry_bucket:
                    compensacao = min(carry, pnl)
                    carry -= compensacao
                base = pnl - compensacao
            elif pnl < 0 and has_carry_bucket:
                carry += -pnl
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
                "aliquota": ALIQUOTA[cls],
                "ir": base * ALIQUOTA[cls],
                "carry": carry,
            })
    return rows
