"""
Configuration constants for B3 data pipeline.
"""

from pathlib import Path
from datetime import datetime

BASE_COTAHIST_URL = (
    "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP"
)
DAILY_COTAHIST_URL = (
    "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_D{date:%d%m%Y}.ZIP"
)
B3_CASH_DIVIDENDS_URL = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetListedCashDividends/{payload}"
B3_STOCK_CORP_ACTIONS_URL = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetListedSupplementCompany/{payload}"
B3_COMPANY_LIST_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/"
    "CompanyCall/GetInitialCompanies/{payload}"
)

# B3's bulk "Arquivos" portal — a two-step download (ask for a token, then spend
# it) with no authentication. It serves the daily consolidated trade file, which
# is where the instruments COTAHIST does not carry live.
B3_FILE_TOKEN_URL = (
    "https://arquivos.b3.com.br/api/download/requestname"
    "?fileName={name}&date={date:%Y-%m-%d}"
)
B3_FILE_DOWNLOAD_URL = "https://arquivos.b3.com.br/api/download/?token={token}"
B3_TRADE_INFO_FILE = "TradeInformationConsolidated"

# Exchange-listed fixed-income ETFs. COTAHIST covers only the BOVESPA segment, and
# B3 books these under RendaFixaPrivada, so they appear in no COTAHIST year at all
# — verified across 2023-2026 with no BDI or market filter. They are ordinary B3
# tickers that trade all day, so a book holding them has a real position the price
# plane simply cannot see; the daily file above is where their marks come from.
#
# ponytail: an explicit list, not a rule. The file's own SgmtNm distinguishes them
# ("FORWARD" vs "CASH"), but that enum is undocumented and not worth keying
# production on for seven tickers. If these proliferate, derive from SgmtNm.
RENDA_FIXA_ETFS = frozenset({
    "LFTB11", "LFTS11", "IMAB11", "IRFM11", "IB5M11", "FIXA11", "B5P211",
})

# B3's daily file has a rolling retention window of roughly 14 months — older
# dates answer 200 with an empty body. Anything not ingested before it rolls off
# is unrecoverable, which is what makes this a daily obligation rather than a
# backfill we can repeat. Bracketed by probe: 2025-05-02 empty, 2025-06-10 present.
EXTERNAL_PRICES_START = datetime(2025, 6, 1).date()

# How far back each run re-checks for gaps. A day skipped by a transient error is
# only recoverable while B3 still serves it, so runs heal recent holes rather than
# resuming from the newest row. Not unbounded: market holidays never have rows, so
# an infinite window would re-probe every past holiday on every run.
#
# This window alone cannot be the retry story. It cannot tell a holiday from an
# outage — both leave no rows — so it must stay short enough that re-probing every
# holiday inside it is cheap, which is exactly what makes it too short to cover a
# long outage. Failures are therefore remembered explicitly, in
# external_price_failures, and retried by date regardless of this window.
EXTERNAL_PRICES_HEAL_DAYS = 45

# When to stop retrying a failed day. Past B3's rolling window the file answers
# 200 with an empty body forever, so a retry can never succeed and the history is
# genuinely gone — the ledger has to give up or it grows a permanent tail that
# costs two round trips per corpse per run. Deliberately looser than the ~14
# months measured (2025-05-02 empty, 2025-06-10 present): the cost of retrying a
# day B3 has already dropped is two wasted requests, while giving up on a day it
# would still have served loses that day for good.
EXTERNAL_PRICES_RETENTION_DAYS = 400

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw"
DB_PATH = PROJECT_ROOT / "b3_market_data.sqlite"

DATA_DIR.mkdir(parents=True, exist_ok=True)

START_YEAR = 1994


def get_current_year() -> int:
    """Return the current year at call time (not import time)."""
    return datetime.now().year


CURRENT_YEAR = get_current_year()  # Keep backward compat; prefer get_current_year() in new code

# COTAHIST CODBDI codes kept from the spot market (TPMERC 010):
#   02 lote padrão (ações e units)   10 direitos e recibos
#   12 fundos imobiliários            13 FIAGRO        14 ETF / cert. de investimento
# This was {"02"} alone, which silently left every FII, FIAGRO and ETF out of the
# price plane — a book holding BTLG11 or IVVB11 had them reported as "sem cotação
# na base" forever, since no other feed backfills them. 13 and 14 are both needed:
# a fund can migrate between them (KNCA11 was 13 through 2025, 14 from 2026).
#
# 96 (fracionário, TPMERC 020) is deliberately NOT here. PETR4F is the same
# security as PETR4 at a smaller lot and closes identically, so storing it would
# duplicate the plane for no new information; it is folded onto the round-lot
# ticker at read time instead (taxcore load_b3.consolidate_fractional for
# positions, platform_app.portfolio_index.canonical for the index).
EQUITY_BDI_CODES = {"02", "10", "12", "13", "14"}

# ── CVM open data portal ──────────────────────────────────────────────────────
CVM_DFP_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/"
CVM_ITR_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/"
CVM_FRE_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/"
CVM_IPE_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/"
CVM_FCA_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/"
CVM_CAD_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/"
CVM_CAD_FILENAME = "cad_cia_aberta.csv"
CVM_DATA_DIR = PROJECT_ROOT / "data" / "cvm"
CVM_DATA_DIR.mkdir(parents=True, exist_ok=True)
CVM_START_YEAR = 2010

# Max age (days) of a filing before its values are considered stale and expired
# from the fundamentals_monthly forward-fill (companies that stop filing must
# not keep their last values forever).
FUNDAMENTALS_MAX_STALENESS_DAYS = 400

# NOTE: This layout dictionary is for reference only and may not exactly match
# the field positions used in parser.py (which hardcodes the correct offsets).
# Do not use this dict for programmatic parsing.
COTAHIST_LAYOUT = {
    "tipo_registro": (0, 2),
    "data_pregao": (2, 10),
    "cod_bdi": (10, 12),
    "cod_negociacao": (12, 24),
    "tipo_mercado": (24, 27),
    "nome_empresa": (27, 39),
    "especificacao_papel": (39, 49),
    "prazo_dias_termo": (49, 52),
    "moeda_referencia": (52, 56),
    "preco_abertura": (56, 69),
    "preco_negocio": (69, 82),
    "preco_maximo": (82, 95),
    "preco_minimo": (95, 108),
    "preco_medio": (108, 121),
    "preco_ultimo_negocio": (121, 134),
    "preco_melhor_oferta_compra": (134, 147),
    "preco_melhor_oferta_venda": (147, 160),
    "total_negocios": (147, 152),
    "quantidade_total_titulos": (152, 170),
    "volume_total_titulos": (170, 188),
    "preco_exercicio": (188, 201),
    "indicador_correcao": (201, 202),
    "data_vencimento": (202, 210),
    "fator_cotacao": (210, 217),
    "preco_exercicio_pontos": (217, 230),
    "cod_isin": (230, 242),
    "num_distribuicao": (242, 245),
}

B3_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

B3_CORP_ACTIONS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

SPLIT_DETECTION_THRESHOLD_HIGH = 1.8
SPLIT_DETECTION_THRESHOLD_LOW = 0.55

# Delay (seconds) each worker thread sleeps between its own sequential HTTP requests.
# With MAX_WORKERS concurrent workers the aggregate request rate ceiling is
# approximately MAX_WORKERS / RATE_LIMIT_DELAY requests/second.
RATE_LIMIT_DELAY = 0.05

# Maximum number of concurrent worker threads for the corporate actions fetch step.
# Increase cautiously -- the B3 API has no published rate limit.
MAX_WORKERS = 10

EVENT_TYPE_CASH_DIVIDEND = "CASH_DIVIDEND"
EVENT_TYPE_JCP = "JCP"
EVENT_TYPE_STOCK_SPLIT = "STOCK_SPLIT"
EVENT_TYPE_REVERSE_SPLIT = "REVERSE_SPLIT"
EVENT_TYPE_BONUS_SHARES = "BONUS_SHARES"

B3_LABEL_DIVIDEND = "DIVIDENDO"
B3_LABEL_JCP = "JRS CAP PROPRIO"
B3_LABEL_RENDIMENTO = "RENDIMENTO"
B3_LABEL_DESDOBRAMENTO = "DESDOBRAMENTO"
B3_LABEL_GRUPAMENTO = "GRUPAMENTO"
B3_LABEL_BONIFICACAO = "BONIFICACAO"

# Non-split labels that affect share count but are NOT standard splits.
# These are stored in skipped_events for manual review.
B3_LABEL_RESG_TOTAL_RV = "RESG TOTAL RV"    # Total share redemption (delisting event)
B3_LABEL_CIS_RED_CAP = "CIS RED CAP"        # Spin-off with capital reduction (complex)
B3_LABEL_INCORPORACAO = "INCORPORACAO"       # Merger/incorporation (complex)
