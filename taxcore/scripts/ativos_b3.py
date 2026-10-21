"""Which tax class a B3 ticker belongs to — by explicit list first, suffix last.

The old rule was "ends in 11 and is not on a ten-name units list → FII". That
taxed every ETF de renda variável (BOVA11, SMAL11, HASH11, GOLD11...) at the FII
20% with no compensation against ações, and every unit missing from the list the
same way. The suffix says only "this is a certificate of some kind": B3 uses 11
for FII cotas, ETF cotas *and* units alike. So the classes that share the 11
suffix are listed here by name, and a *11 ticker on none of the lists is still
read as a FII — most *11 tickers are — but reported as **presumed**
(`fii_presumido`) so the report can say so instead of guessing silently.

The lists live in a module under scripts/, not a CSV under data/, because the
platform runs taxcore in a throwaway workspace that copies `scripts/` and writes
only the tenant's own book into `data/` (platform_app/b3run.seed_workspace).

Legal basis per class:

- **Ações** (incl. units): 15%, isenção de R$ 20.000/mês de alienações (Lei
  11.033/2004, art. 3º, I). Units are certificados de depósito de ações (an ON +
  PN bundle) traded in the mercado à vista de ações; brokers, B3 and the usual
  practice treat them as ações for the isenção. The Receita has not published a
  solução de consulta excluding them — but neither one including them, so this is
  the prevailing reading, not settled law.
- **ETF de renda variável**: 15%, *no* isenção (Lei 13.043/2014, art. 2º;
  Solução de Consulta Cosit 145/2021 limits the R$ 20k to "ativos da espécie
  ações"). Same "operações comuns" pool as ações — losses offset each other.
- **BDR** (suffixes 31-35, 39 = BDR de ETF): 15%, no isenção, operações comuns
  (Solução de Consulta Cosit 39/2022).
- **FII**: 20%, no isenção, own loss bucket (Lei 8.668/1993, art. 18; IN RFB
  1.585/2015). XFIX11 is an ETF *of FIIs*, and B3 states the FII 20% applies to
  its cotas, so it is apurado on the FII line.
- **ETF de renda fixa**: tributado na fonte (Lei 13.043/2014, art. 2º), outside
  the renda variável apuração.
"""
from __future__ import annotations

from scripts.load_b3 import _RF_PREFIX_RE as RF_PREFIX_RE

# ETFs de renda variável listed on B3 → (gestor/índice), for the Bens e Direitos
# discriminação. Add a ticker here when it shows up in someone's history.
ETF_RV: dict[str, str] = {
    "BOVA11": "iShares Ibovespa", "BRAX11": "iShares IBrX-100",
    "ECOO11": "iShares Carbono Eficiente", "SMAL11": "iShares Small Cap",
    "IVVB11": "iShares S&P 500", "BOVV11": "It Now Ibovespa",
    "PIBB11": "It Now IBrX-50", "DIVO11": "It Now IDIV",
    "FIND11": "It Now Financeiro", "GOVE11": "It Now Governança",
    "MATB11": "It Now Materiais Básicos", "ISUS11": "It Now ISE",
    "SPXI11": "It Now S&P 500", "TECK11": "It Now Tecnologia",
    "BOVB11": "Bradesco Ibovespa", "BOVX11": "Trend Ibovespa",
    "XBOV11": "Caixa Ibovespa", "GOLD11": "Trend Ouro",
    "NASD11": "Trend Nasdaq-100", "XINA11": "Trend China",
    "EURP11": "Trend Europa", "ACWI11": "Trend ACWI",
    "HASH11": "Hashdex Nasdaq Crypto Index", "BITH11": "Hashdex Bitcoin",
    "ETHE11": "Hashdex Ethereum", "DEFI11": "Hashdex DeFi",
    "WEB311": "Hashdex Web3", "QBTC11": "QR Bitcoin", "QETH11": "QR Ethereum",
    "ALUG11": "Investo REITs EUA", "WRLD11": "Investo Global",
}

# ETF whose portfolio is FII cotas: an ETF in Bens e Direitos, a FII in the apuração.
ETF_FII: dict[str, str] = {"XFIX11": "Trend IFIX"}

ETF_RF: dict[str, str] = {
    "LFTB11": "Tesouro Selic", "IMAB11": "It Now IMA-B", "IRFM11": "It Now IRF-M",
    "B5P211": "It Now IMA-B5", "IB5M11": "It Now IMA-B5+", "FIXA11": "Mirae Renda Fixa Pré",
}

# Stock UNITS (ON+PN bundles) ending in 11: ações for IRPF (grupo 03 código 01).
UNITS: set[str] = {
    "SAPR11", "TAEE11", "KLBN11", "BPAC11", "ENGI11", "ALUP11", "SANB11", "AESB11",
    "JPSA11", "ENMT11", "BRBI11", "IGTI11", "CPLE11", "TIET11", "BIDI11", "RNEW11",
    "PPLA11", "SULA11", "MODL11",
}
UNITS_NOT_FII = UNITS  # the name irpf_2026 has always exported

# FIIs known by name. Not exhaustive and not meant to be — B3 lists hundreds — it
# only keeps the common ones out of the "presumed" warning.
FII: set[str] = {
    "MXRF11", "HGLG11", "KNRI11", "XPML11", "VISC11", "HGRU11", "BTLG11", "KNCR11",
    "KNIP11", "CPTS11", "IRDM11", "RECR11", "HGCR11", "VGIP11", "BCFF11", "HFOF11",
    "RBRF11", "XPLG11", "VILG11", "BRCO11", "LVBI11", "PVBI11", "HGRE11", "JSRE11",
    "RBRP11", "TRXF11", "ALZR11", "HSML11", "MALL11", "GGRC11", "KNSC11", "VRTA11",
    "RZTR11", "TGAR11", "HCTR11", "DEVA11", "BCRI11", "RBRR11", "MCCI11", "KNHY11",
    "XPIN11", "RBVA11", "GALG11", "BTCI11", "HABT11", "VGHF11", "TVRI11", "RECT11",
    "BRCR11", "GTWR11", "HGBS11", "VINO11", "FIIB11", "KFOF11", "OUJP11", "HGPO11",
    "RBRL11", "SDIL11", "GARE11", "BTAL11", "CVBI11", "MGFF11", "XPCI11", "KISU11",
}

BDR_SUFFIXES = ("31", "32", "33", "34", "35", "39")


def asset_class(t: str) -> str:
    """Map a ticker to its tax asset class (AÇÃO / FII / BDR / ETF_RV / ETF_RF / RF)."""
    t = t.upper()
    if t in ETF_RV:
        return "ETF_RV"
    if t in ETF_RF:
        return "ETF_RF"
    # Renda fixa contracts carry a contract code, not a ticker; booked as a stock
    # sale they would inflate the month's AÇÃO gross and blow the isenção.
    if RF_PREFIX_RE.match(t):
        return "RF"
    if t in UNITS:
        return "AÇÃO"
    if t.endswith("11"):
        return "FII"  # by name (FII / ETF_FII) or presumed — see fii_presumido
    if t.endswith(BDR_SUFFIXES):
        return "BDR"
    return "AÇÃO"


def fii_presumido(t: str) -> bool:
    """A *11 read as FII only because it is on none of the lists above."""
    t = t.upper()
    return asset_class(t) == "FII" and t not in FII and t not in ETF_FII


# IRPF Bens e Direitos group/código mapping (per RFB Instrução Normativa).
# Tuple = (group_code, item_code, label_for_discriminação)
def irpf_classification(ticker: str) -> tuple[str, str, str]:
    t = ticker.upper()
    etf = ETF_RV.get(t) or ETF_FII.get(t)
    if etf:
        return ("07", "09", f"Cotas de Fundo de Índice (ETF) - {t} ({etf})")
    if t in ETF_RF:
        return ("04", "02", f"Cotas de ETF de Renda Fixa - {t} ({ETF_RF[t]})")
    cls = asset_class(t)
    if cls == "FII":
        presumido = " — presumido pelo final 11, confira" if fii_presumido(t) else ""
        return ("07", "03", f"Cotas de Fundo de Investimento Imobiliário (FII){presumido}")
    if cls == "BDR":
        return ("03", "09", "BDR — Brazilian Depositary Receipt")
    if t in UNITS:
        return ("03", "01", "Ações (Unit)")
    return ("03", "01", "Ações")
