"""Which tax class a B3 ticker belongs to, for the backtest's apuração.

A deliberate copy of `taxcore/scripts/ativos_b3.py` (`asset_class` and the lists
it reads). The engine and taxcore run in separate venvs and cannot import each
other — the same reason `markets.py` copies taxcore's rates — so the rule is
written twice and `tests/test_asset_classes.py` runs taxcore's own classifier
over every name listed here and fails on the first disagreement. Change one,
change the other.

Why a list and not the plane: the instrument master carries no asset type for
B3. `instruments_backfill` writes `kind = "EQUITY"` for every B3 row (COTAHIST is
joined to CVM, which knows companies, not funds), and on CI the table is empty.
The suffix says only "a certificate of some kind" — B3 uses 11 for FII cotas, ETF
cotas and units alike — so the classes that share it are named, exactly as
taxcore names them, and the result does not depend on any table being loaded.

What the classes mean for the tax (taxcore `apuracao.REGRAS`):

  AÇÃO (incl. units)  15%, R$20k monthly isenção on the month's ações sales
  ETF_RV, BDR         15%, no isenção (Lei 13.043/2014 art. 2º; Cosit 145/2021,
                      39/2022) — same operações comuns loss pool as ações
  FII                 20%, no isenção, a loss pool of its own
  ETF_RF              tributado na fonte; taxcore leaves it out of the apuração

A *11 on none of the lists is read as a FII, as taxcore reads it (it calls that
"presumido" and warns). Every B3 ETF a built-in or a user sleeve holds is listed.
"""
from __future__ import annotations

# taxcore ativos_b3.ETF_RV — ETFs de renda variável.
ETF_RV = frozenset({
    "BOVA11", "BRAX11", "ECOO11", "SMAL11", "IVVB11", "BOVV11", "PIBB11", "DIVO11",
    "FIND11", "GOVE11", "MATB11", "ISUS11", "SPXI11", "TECK11", "BOVB11", "BOVX11",
    "XBOV11", "GOLD11", "NASD11", "XINA11", "EURP11", "ACWI11", "HASH11", "BITH11",
    "ETHE11", "DEFI11", "WEB311", "QBTC11", "QETH11", "ALUG11", "WRLD11",
})
# taxcore ativos_b3.ETF_RF — ETFs de renda fixa.
ETF_RF = frozenset({"LFTB11", "IMAB11", "IRFM11", "B5P211", "IB5M11", "FIXA11"})
# taxcore ativos_b3.UNITS — ON+PN bundles ending in 11, ações for the IRPF.
UNITS = frozenset({
    "SAPR11", "TAEE11", "KLBN11", "BPAC11", "ENGI11", "ALUP11", "SANB11", "AESB11",
    "JPSA11", "ENMT11", "BRBI11", "IGTI11", "CPLE11", "TIET11", "BIDI11", "RNEW11",
    "PPLA11", "SULA11", "MODL11",
})
# taxcore ativos_b3.BDR_SUFFIXES — 31-35, and 39 for a BDR de ETF.
BDR_SUFFIXES = ("31", "32", "33", "34", "35", "39")


def b3_class(ticker: str) -> str:
    """AÇÃO / ETF_RV / ETF_RF / BDR / FII, in taxcore's order of tests.

    taxcore also returns "RF" for renda fixa contract codes (CDB..., TESOURO...);
    those are never a column of a backtest, so the engine has no such branch —
    and `CDI_ASSET`, the engine's own cash sleeve, falls through to AÇÃO here
    exactly as it does there.
    """
    t = ticker.strip().upper().removesuffix(".SA")
    if t in ETF_RV:
        return "ETF_RV"
    if t in ETF_RF:
        return "ETF_RF"
    if t in UNITS:
        return "AÇÃO"
    if t.endswith("11"):
        return "FII"
    if t.endswith(BDR_SUFFIXES):
        return "BDR"
    return "AÇÃO"
