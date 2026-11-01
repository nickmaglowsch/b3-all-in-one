"""Derived and sanitised CVM statement metrics, applied to the parser's wide frame.

net_debt = Empréstimos e Financiamentos CP + LP − Caixa e Equivalentes −
Aplicações Financeiras CP (2.01.04 + 2.02.01 − 1.01.01 − 1.01.02). Debêntures
sit inside 2.01.04/2.02.01; leases (2.01.05/2.02.05) are not debt here. Without
1.01.02 a builder or hospital parking cash in CDBs (CYRE, EZTC, RDOR) read as
over-levered. Rows where none of debt/cash exist stay NULL: bank-chart filers
report no 1.01.01/2.01.04/2.02.01, and a fake net_debt of 0 for a bank is a
wrong value, not a neutral one.

All-zero blocks are an empty form, not a company with no revenue: TIMS's FY2024
ITRs/DFP and GEPA/GPAR filed consolidated statements of zeros (25 DFP/ITR rows on
prod, 2026-11). Stored as 0 they won "newest period" and fed zero earnings and
zero-year TTMs to every strategy. They are stored NULL.

Note `ebitda` is EBIT (the DRE line "Resultado Antes do Resultado Financeiro e
dos Tributos"); the column keeps its name for its consumers.
"""
from __future__ import annotations

import pandas as pd

# intermediate accounts the parser pivots out (cvm_parser.*_ACCOUNT_MAP)
_PARTS = ["_short_debt", "_long_debt", "_cash", "_st_invest"]

_ZERO_BLOCKS = (
    ("revenue", "net_income", "ebitda"),
    ("total_assets", "equity", "net_debt"),
)


def blank_zero_statements(merged: pd.DataFrame) -> None:
    """NULL a block (in place) where every metric of it is filed and exactly 0.
    One zero alone is a value (a net debt of 0 is real); a whole block is not."""
    for block in _ZERO_BLOCKS:
        cols = [c for c in block if c in merged.columns]
        if len(cols) < 2:
            continue
        zero = merged[cols].eq(0).all(axis=1)
        merged.loc[zero, cols] = None


def derive(merged: pd.DataFrame) -> None:
    """Add net_debt, blank all-zero blocks, drop the intermediate accounts (in place)."""
    parts = merged.reindex(columns=_PARTS)
    net = (parts["_short_debt"].fillna(0) + parts["_long_debt"].fillna(0)
           - parts["_cash"].fillna(0) - parts["_st_invest"].fillna(0))
    merged["net_debt"] = net.where(parts[_PARTS[:3]].notna().any(axis=1))
    blank_zero_statements(merged)
    merged.drop(columns=[c for c in _PARTS if c in merged.columns], inplace=True)
