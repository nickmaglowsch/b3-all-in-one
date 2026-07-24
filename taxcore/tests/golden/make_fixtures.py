#!/usr/bin/env python3
"""Generate the synthetic B3 fixtures this repo ships.

Writes `b3_imports/*.xlsx` (the 16 files the golden suite, the platform e2e
test and scripts/dev_seed.sh expect) plus `data/cost_basis_overrides.csv`.

EVERYTHING HERE IS FABRICATED. The only real data are the ticker symbols,
company names, broker names and issuer names — public market information, kept
real so the positions can be priced from the vendored Fundamentus snapshots.
Every quantity, price, date, income amount and the whole holdings mix is
invented. No number comes from anyone's brokerage account.

The tables below are laid out to exercise every branch of scripts/load_b3.py
and scripts/irpf_2026.py — see tests/golden/README.md for the coverage map.

Run from `taxcore/`:

    uv run python tests/golden/make_fixtures.py
    uv run python -m scripts.load_b3
    uv run python -m scripts.irpf_2026 --year 2025 > tests/golden/expected/irpf-2025.md
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
IMPORTS = ROOT / "b3_imports"
OVERRIDES_CSV = ROOT / "data" / "cost_basis_overrides.csv"

ITAU = "ITAU CV S/A"
ORAMA = "ORAMA DTVM S.A."
XPI = "XP INVESTIMENTOS CCTVM S/A"

VISTA = "Mercado à Vista"
FRAC = "Mercado Fracionário"
RFIX = "Renda Fixa"
FUT = "Futuro"

# Movimentação spells the security out as "TICKER - COMPANY NAME".
NAME = {
    "PETR4": "PETROLEO BRASILEIRO S/A PETROBRAS",
    "VALE3": "VALE S.A.",
    "ITUB4": "ITAU UNIBANCO HOLDING S.A.",
    "BBAS3": "BANCO DO BRASIL S/A",
    "WEGE3": "WEG S.A.",
    "ABEV3": "AMBEV S.A.",
    "B3SA3": "B3 S.A. - BRASIL BOLSA BALCAO",
    "CMIG4": "CIA ENERGETICA DE MINAS GERAIS",
    "SBSP3": "CIA SANEAMENTO BASICO EST SAO PAULO",
    "EGIE3": "ENGIE BRASIL ENERGIA S.A.",
    "GOAU4": "METALURGICA GERDAU S.A.",
    "TRPL4": "ISA CTEEP - CIA TRANSMISSAO ENERGIA ELETR PAULISTA",
    "ISAE4": "ISA ENERGIA BRASIL S.A.",
    "TAEE11": "TAESA - TRANSMISSORA ALIANCA DE ENERGIA ELETRICA",
    "KLBN11": "KLABIN S.A.",
    "RENT3": "LOCALIZA RENT A CAR S.A.",
    "MXRF11": "MAXI RENDA FDO INV IMOB",
    "HGLG11": "CSHG LOGISTICA FDO INV IMOB",
    "KNRI11": "KINEA RENDA IMOBILIARIA FDO INV IMOB",
    "XPML11": "XP MALLS FDO INV IMOB",
    "VISC11": "VINCI SHOPPING CENTERS FDO INV IMOB",
    "HGRU11": "CSHG RENDA URBANA FDO INV IMOB",
    "BTLG11": "BTG PACTUAL LOGISTICA FDO INV IMOB",
    "RECR11": "REC RECEBIVEIS IMOBILIARIOS FDO INV IMOB",
    "KNCR11": "KINEA RENDIMENTOS IMOBILIARIOS FDO INV IMOB",
    "IVVB11": "ISHARES SP 500 FDO INV INDICE",
    "LFTB11": "IT NOW ID TESOURO SELIC FDO INDICE",
}

# --------------------------------------------------------------------------
# Negociação: (date, side, market, ticker, qty, price). Broker is always ITAU.
# 2024 accumulates, 2025 carries the sells that drive the IRPF grid, 2026 is
# top-up buys only (so the TLH planner sees an empty current month).
# --------------------------------------------------------------------------
TRADES = [
    # --- 2024 H1 ---
    ("2024-01-09", "Compra", VISTA, "MXRF11", 1500, 10.35),
    ("2024-01-16", "Compra", VISTA, "IVVB11", 40, 340.00),
    ("2024-01-18", "Compra", VISTA, "ITUB4", 400, 31.20),
    ("2024-01-30", "Compra", VISTA, "RENT3", 300, 52.40),
    ("2024-02-06", "Compra", VISTA, "HGLG11", 60, 152.30),
    ("2024-02-08", "Compra", RFIX, "LFTB11", 100, 108.40),
    ("2024-02-15", "Compra", VISTA, "PETR4", 300, 27.80),
    ("2024-02-20", "Compra", VISTA, "TRPL4", 300, 22.40),
    ("2024-02-27", "Compra", VISTA, "WEGE3", 100, 40.10),
    ("2024-03-05", "Compra", VISTA, "B3SA3", 600, 11.35),
    ("2024-03-12", "Compra", VISTA, "VALE3", 150, 58.40),
    ("2024-03-19", "Compra", VISTA, "TAEE11", 250, 34.20),
    ("2024-03-26", "Compra", VISTA, "KNRI11", 80, 146.50),
    ("2024-04-09", "Compra", VISTA, "BBAS3", 500, 27.10),
    ("2024-04-16", "Compra", VISTA, "CMIG4", 1000, 9.85),
    ("2024-04-16", "Compra", FRAC, "CMIG4F", 34, 9.87),
    ("2024-04-23", "Compra", VISTA, "XPML11", 120, 99.40),
    ("2024-05-08", "Compra", VISTA, "EGIE3", 200, 39.50),
    ("2024-05-14", "Compra", VISTA, "ABEV3", 800, 12.85),
    ("2024-05-21", "Compra", VISTA, "VISC11", 100, 112.60),
    ("2024-06-04", "Compra", VISTA, "KLBN11", 600, 20.30),
    ("2024-06-11", "Compra", VISTA, "PETR4", 400, 29.50),
    ("2024-06-18", "Compra", VISTA, "KNCR11", 100, 103.20),
    ("2024-06-25", "Compra", VISTA, "SBSP3", 250, 34.80),
    # --- 2024 H2 ---
    ("2024-07-02", "Compra", VISTA, "GOAU4", 1500, 8.90),
    ("2024-07-09", "Compra", VISTA, "RECR11", 150, 88.40),
    ("2024-07-16", "Compra", VISTA, "WEGE3", 100, 51.60),
    ("2024-07-23", "Compra", VISTA, "RENT3", 200, 44.10),
    ("2024-08-06", "Compra", VISTA, "ISAE4", 200, 25.10),
    ("2024-08-14", "Compra", VISTA, "ITUB4", 200, 34.90),
    ("2024-08-20", "Compra", VISTA, "HGLG11", 40, 141.80),
    ("2024-09-10", "Compra", VISTA, "B3SA3", 300, 13.05),
    ("2024-09-17", "Compra", VISTA, "HGRU11", 90, 121.40),
    ("2024-09-24", "Compra", VISTA, "TAEE11", 150, 36.40),
    ("2024-10-08", "Compra", VISTA, "VALE3", 100, 66.20),
    ("2024-10-22", "Compra", VISTA, "BTLG11", 110, 98.70),
    ("2024-11-05", "Compra", VISTA, "XPML11", 60, 104.10),
    ("2024-11-19", "Compra", VISTA, "BBAS3", 400, 23.30),
    ("2024-11-26", "Compra", VISTA, "IVVB11", 20, 385.50),
    ("2024-12-03", "Compra", VISTA, "EGIE3", 150, 36.20),
    ("2024-12-10", "Compra", FRAC, "GOAU4F", 47, 9.05),
    # --- 2025 ---
    ("2025-01-14", "Compra", VISTA, "CMIG4", 500, 10.40),
    ("2025-01-28", "Compra", RFIX, "LFTB11", 60, 114.20),
    ("2025-02-11", "Compra", VISTA, "ABEV3", 400, 14.60),
    ("2025-02-19", "Venda", VISTA, "B3SA3", 200, 15.40),      # isento month
    ("2025-02-19", "Venda", VISTA, "ABEV3", 150, 15.10),      # isento month
    ("2025-03-25", "Venda", VISTA, "XPML11", 60, 108.30),     # FII gain
    ("2025-04-15", "Venda", VISTA, "PETR4", 300, 38.90),      # > R$20k month
    ("2025-04-15", "Venda", VISTA, "VALE3", 200, 71.50),      # > R$20k month
    ("2025-05-06", "Compra", VISTA, "KNRI11", 40, 151.20),
    ("2025-05-20", "Compra", VISTA, "PETR4", 300, 33.10),
    ("2025-07-15", "Venda", VISTA, "RENT3", 500, 40.30),      # taxable loss month
    ("2025-08-12", "Compra", VISTA, "SBSP3", 150, 36.70),
    ("2025-08-19", "Venda", VISTA, "VISC11", 50, 96.40),      # FII loss
    ("2025-09-09", "Venda", VISTA, "KLBN11", 300, 17.60),     # isento loss month
    ("2025-09-16", "Compra", VISTA, "ISAE4", 100, 27.80),
    ("2025-10-07", "Compra", VISTA, "KLBN11", 300, 18.20),
    ("2025-10-21", "Venda", VISTA, "IVVB11", 15, 402.80),     # ETF RV
    ("2025-11-12", "Venda", VISTA, "ITUB4", 400, 37.20),      # taxable, offset
    ("2025-11-12", "Venda", VISTA, "TAEE11", 250, 38.90),
    ("2025-11-25", "Venda", RFIX, "LFTB11", 40, 120.10),      # ETF RF
    ("2025-12-16", "Venda", VISTA, "KNCR11", 100, 112.40),    # FII gain, offset
    # --- 2026 (buys only: the TLH planner must see an empty current month) ---
    ("2026-01-22", "Compra", VISTA, "TAEE11", 250, 38.10),
    ("2026-02-10", "Compra", VISTA, "VALE3", 200, 74.20),
    ("2026-03-05", "Compra", VISTA, "ITUB4", 400, 35.60),
    ("2026-04-14", "Compra", VISTA, "XPML11", 60, 103.50),
    ("2026-04-28", "Compra", VISTA, "MXRF11", 300, 9.60),
    ("2026-05-12", "Compra", VISTA, "VISC11", 50, 101.20),
    ("2026-05-12", "Compra", FRAC, "ABEV3F", 26, 15.85),
]

# Option premiums (open/close, never assigned) + two assignments, plus a pair of
# mini-index futures. Negociação only — options/futures never reach positions.csv
# under the default flags, which is what makes --all-markets widen it.
# (date, side, market, code, qty, price, maturity)
DERIVATIVES = [
    ("2024-09-19", "Compra", "Exercício de Opção de Venda", "BBASU200E", 200, 20.00, "2024-09-20"),
    ("2025-03-18", "Venda", "Exercício de Opção de Compra", "CMIGC120E", 300, 12.00, "2025-03-21"),
    ("2025-05-16", "Venda", "Opção de Compra sobre Ações", "PETRF460", 300, 1.28, "2025-06-20"),
    ("2025-05-16", "Compra", "Opção de Compra sobre Ações", "PETRF500", 300, 0.61, "2025-06-20"),
    ("2025-08-14", "Venda", "Opção de Venda sobre Ações", "VALER780", 200, 2.15, "2025-09-19"),
    ("2025-02-05", "Compra", FUT, "WINJ25", 4, 126300.00, "2025-04-16"),
    ("2025-02-06", "Venda", FUT, "WINJ25", 2, 129150.00, "2025-04-16"),
]

# The Movimentação settlement rows that resolve each exercise's underlying.
# load_b3 matches them on qty + price within a 5-day window.
EXERCISE_SETTLEMENTS = [
    ("2024-09-23", "Credito", "BBAS3", 200, 20.00),
    ("2025-03-20", "Debito", "CMIG4", 300, 12.00),
]

# Equity income: (ticker, Movimentação, first amount, held qty, [pay dates]).
# Each successive payment is bumped 3.7% so no two rows collide on the
# cash_events dedup key. TRPL4 rows must fold into ISAE4 (TICKER_RENAMES).
EQUITY_INCOME = [
    ("PETR4", "Dividendo", 612.40, 700,
     ["2024-03-21", "2024-08-22", "2025-03-20", "2025-08-21", "2026-03-19"]),
    ("VALE3", "Dividendo", 486.20, 250,
     ["2024-03-08", "2024-09-06", "2025-03-07", "2025-09-05", "2026-03-06"]),
    ("ITUB4", "Juros Sobre Capital Próprio", 187.30, 600,
     ["2024-04-02", "2024-10-01", "2025-04-01", "2025-10-01", "2026-04-01"]),
    ("BBAS3", "Juros Sobre Capital Próprio", 214.80, 1100,
     ["2024-06-14", "2024-12-13", "2025-06-13", "2025-12-12", "2026-05-15"]),
    ("ABEV3", "Dividendo", 268.40, 1076,
     ["2024-07-01", "2025-01-02", "2025-07-01", "2026-01-02"]),
    ("B3SA3", "Juros Sobre Capital Próprio", 132.60, 700,
     ["2024-05-30", "2024-11-29", "2025-05-30", "2025-11-28"]),
    ("CMIG4", "Dividendo", 521.90, 1234,
     ["2024-06-28", "2025-06-27", "2026-05-29"]),
    ("TAEE11", "Dividendo", 342.10, 400,
     ["2024-08-30", "2025-02-28", "2025-08-29", "2026-02-27"]),
    ("KLBN11", "Juros Sobre Capital Próprio", 118.90, 600,
     ["2024-09-13", "2025-03-14", "2025-09-12", "2026-03-13"]),
    ("GOAU4", "Dividendo", 176.50, 1621,
     ["2024-10-11", "2025-04-11", "2025-10-10"]),
    ("SBSP3", "Dividendo", 143.70, 400, ["2024-12-20", "2025-12-19"]),
    ("EGIE3", "Juros Sobre Capital Próprio", 165.20, 350,
     ["2024-11-08", "2025-05-09", "2025-11-07"]),
    ("WEGE3", "Juros Sobre Capital Próprio", 61.40, 300,
     ["2024-08-09", "2025-02-14", "2025-08-08", "2026-02-13"]),
    ("RENT3", "Dividendo", 84.30, 500, ["2024-04-25", "2025-04-24"]),
    ("TRPL4", "Juros Sobre Capital Próprio", 244.60, 500,
     ["2024-05-17", "2024-11-15"]),
    ("TRPL4", "Dividendo", 158.30, 500, ["2025-01-24"]),
    ("ISAE4", "Dividendo", 191.40, 600, ["2025-07-18", "2026-01-23"]),
]

# FII monthly "Rendimento": (ticker, first month, last month, first amount, qty).
FII_INCOME = [
    ("MXRF11", "2024-02", "2026-05", 148.50, 1500),
    ("HGLG11", "2024-03", "2026-05", 96.20, 100),
    ("KNRI11", "2024-04", "2026-05", 88.40, 80),
    ("XPML11", "2024-05", "2026-05", 92.10, 180),
    ("VISC11", "2024-06", "2026-05", 71.30, 100),
    ("KNCR11", "2024-07", "2025-12", 108.60, 100),
    ("RECR11", "2024-08", "2026-05", 142.70, 150),
    ("HGRU11", "2024-10", "2026-05", 84.90, 90),
    ("BTLG11", "2024-11", "2026-05", 79.50, 110),
]

# Everything else Movimentação carries, as literal rows:
# (date, direction, Movimentação, ticker, qty, preço unitário, valor).
# "-" means B3 leaves the cell empty. Quantity-only corporate actions carry no
# value; Empréstimo (BTC lending) and the custody-admin rows must be skipped.
MISC_EVENTS = [
    # amortizations (reduce cost basis, qty untouched)
    ("2024-10-15", "Credito", "Amortização", "MXRF11", 1500, "-", 145.00),
    ("2025-04-15", "Credito", "Amortização", "MXRF11", 1500, "-", 180.00),
    # broker / withholding refunds — cash, but not investment income
    ("2025-06-30", "Credito", "Reembolso", "PETR4", 0, "-", 37.85),
    ("2026-02-27", "Credito", "Reembolso", "VALE3", 0, "-", 52.40),
    # quantity-only corporate actions
    ("2025-04-22", "Credito", "Desdobro", "WEGE3", 100, "-", "-"),
    ("2025-06-10", "Credito", "Bonificação em Ativos", "GOAU4", 75, 8.90, "-"),
    ("2025-06-11", "Debito", "Fração em Ativos", "GOAU4", 1, "-", "-"),
    ("2025-06-12", "Credito", "Leilão de Fração", "GOAU4", 1, 9.87, 9.87),
    # BTC lending — skipped even though some rows carry a fee credit
    ("2024-05-31", "Credito", "Empréstimo", "PETR4", 700, "-", 3.16),
    ("2024-08-30", "Credito", "Empréstimo", "PETR4", 700, "-", "-"),
    ("2024-11-29", "Credito", "Empréstimo", "BBAS3", 1100, "-", 12.44),
    ("2025-01-31", "Credito", "Empréstimo", "VALE3", 250, "-", "-"),
    ("2025-05-30", "Credito", "Empréstimo", "BBAS3", 1100, "-", 8.71),
    ("2025-09-30", "Credito", "Empréstimo", "GOAU4", 1621, "-", 21.05),
    ("2026-01-30", "Credito", "Empréstimo", "PETR4", 700, "-", 4.92),
    ("2026-04-30", "Credito", "Empréstimo", "CMIG4", 1234, "-", 6.38),
    # custody admin / subscription noise — all skipped by MOV_CATEGORY
    ("2024-04-30", "Credito", "Transferência", "ITUB4", 400, "-", "-"),
    ("2024-09-30", "Credito", "Atualização", "XPBR31", 10, "-", "-"),
    ("2025-02-28", "Credito", "Direito de Subscrição", "MXRF11", 128, "-", "-"),
    ("2025-03-31", "Debito", "Direitos de Subscrição - Não Exercido", "MXRF11", 128, "-", "-"),
    ("2025-07-31", "Debito", "Cobrança de Taxa Semestral", "IVVB11", 45, "-", 14.60),
    ("2026-03-31", "Credito", "Transferência", "TAEE11", 400, "-", "-"),
]

# Renda fixa lives only in Movimentação, keyed by the "PREFIX - code - issuer"
# Produto string that scripts/irpf_2026.py::renda_fixa_year_end() parses.
# BANCO SINCERO is invented, so the report falls back to "(verificar no informe)".
RENDA_FIXA = [
    ("2024-03-14", "Credito", "APLICAÇÃO", "CDB - CDB24C7HQ2L - BANCO ORIGINAL S/A", 2500000, 0.01, 25000.00),
    ("2026-03-16", "Debito", "VENCIMENTO", "CDB - CDB24C7HQ2L - BANCO ORIGINAL S/A", 2500000, 0.0121, 30250.00),
    ("2025-06-09", "Credito", "APLICAÇÃO", "CDB - CDB25F3MPT8 - BANCO C6 S.A.", 1800000, 0.01, 18000.00),
    ("2024-08-19", "Credito", "APLICAÇÃO", "LCI - LCI24H9RKV3 - BANCO FIBRA S/A", 1200000, 0.01, 12000.00),
    ("2026-02-19", "Debito", "VENCIMENTO", "LCI - LCI24H9RKV3 - BANCO FIBRA S/A", 1200000, 0.01165, 13980.00),
    ("2024-12-05", "Credito", "COMPRA / VENDA", "CRI - 24L0000031 - OPEA SECURITIZADORA S/A", 8, 1035.42, 8283.36),
    ("2025-05-15", "Credito", "AMORTIZAÇÃO", "CRI - 24L0000031 - OPEA SECURITIZADORA S/A", 8, 51.59, 412.75),
    ("2025-05-15", "Credito", "PAGAMENTO DE JUROS", "CRI - 24L0000031 - OPEA SECURITIZADORA S/A", 8, 12.05, 96.40),
    ("2025-01-20", "Credito", "APLICAÇÃO", "CDB - CDB25A4TZW6 - BANCO SINCERO S/A", 950000, 0.01, 9500.00),
    ("2026-01-15", "Debito", "RETIRADA DE CUSTÓDIA", "CDB - CDB25A4TZW6 - BANCO SINCERO S/A", 950000, 0, 0),
    ("2025-04-08", "Credito", "APLICAÇÃO", "RDB - RDB25D8LPQ4 - BANCO PAN S/A", 1500000, 0.01, 15000.00),
    ("2026-04-08", "Debito", "RESGATE ANTECIPADO", "RDB - RDB25D8LPQ4 - BANCO PAN S/A", 1500000, 0.010807, 16210.00),
]

OVERRIDES_TEXT = """\
# Manual cost-basis corrections for events the B3 reports cannot capture
# directly: IPO reservation deliveries (Transferência - Liquidação with empty
# Preço), spin-off allocations (Cisão / Incorporação), transfers from another
# broker (Atualização), etc.
#
# SYNTHETIC — invented alongside b3_imports/*.xlsx by tests/golden/make_fixtures.py.
#
# Columns:
#   date     — YYYY-MM-DD
#   ticker   — B3 ticker the row applies to
#   kind     — 'buy'  : synthetic Compra trade at the given (qty, price)
#              'sell' : synthetic Venda trade
#              'cost_adjust' : reduces ticker's cost basis by `amount` (qty unchanged)
#                              (use to record cisão basis transferring OUT of parent)
#   qty      — share count (signed implicitly by `kind`)
#   price    — R$ per share (omit for cost_adjust)
#   amount   — R$ for cost_adjust rows (positive = basis reduction)
#   note     — free-text source / reasoning
date,ticker,kind,qty,price,amount,note
2024-09-30,XPBR31,buy,10.72,84.17,,Cisao sintetica ITUB4 -> XPBR31: 4.64% da base em 10.72 BDRs
2024-09-30,ITUB4,cost_adjust,,,902.30,Cisao sintetica: base transferida de ITUB4 para XPBR31
"""

NEG_COLS = ["Data do Negócio", "Tipo de Movimentação", "Mercado", "Prazo/Vencimento",
            "Instituição", "Código de Negociação", "Quantidade", "Preço", "Valor"]
MOV_COLS = ["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição",
            "Quantidade", "Preço unitário", "Valor da Operação"]


def br(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def produto(ticker: str) -> str:
    return f"{ticker} - {NAME[ticker]}"


def in_range(iso: str, lo: str, hi: str) -> bool:
    return lo <= iso <= hi


def months(first: str, last: str):
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    while (y, m) <= (ly, lm):
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def build_negociacao() -> tuple[list, list]:
    """(spot rows, derivative rows) — each already in NEG_COLS order."""
    spot = [
        [br(iso), side, market, "-", ITAU, ticker, qty, price, round(qty * price, 2)]
        for iso, side, market, ticker, qty, price in TRADES
    ]
    deriv = [
        [br(iso), side, market, br(mat), ORAMA, code, qty, price, round(qty * price, 2)]
        for iso, side, market, code, qty, price, mat in DERIVATIVES
    ]
    return spot, deriv


def build_movimentacao() -> list[list]:
    """Every Movimentação row except renda fixa, tagged with its ISO date at [0]
    so the file splitter can partition by period."""
    rows: list[list] = []

    def add(iso, direction, mov_type, prod, inst, qty, price, valor):
        rows.append([iso, direction, br(iso), mov_type, prod, inst, qty, price, valor])

    # Settlement mirrors of every Negociação trade (T+2), the way B3 books them.
    # These are what let load_b3 resolve an exercise's underlying ticker.
    for iso, side, _market, ticker, qty, price in TRADES:
        settle = (date.fromisoformat(iso) + timedelta(days=2)).isoformat()
        # B3 books the settlement under the round-lot code even for *F orders.
        add(settle, "Credito" if side == "Compra" else "Debito",
            "Transferência - Liquidação", produto(ticker.removesuffix("F")),
            ITAU, float(qty), price, round(qty * price, 2))
    for iso, direction, ticker, qty, price in EXERCISE_SETTLEMENTS:
        add(iso, direction, "Transferência - Liquidação", produto(ticker), ORAMA,
            float(qty), price, round(qty * price, 2))

    for ticker, mov_type, base, held, dates in EQUITY_INCOME:
        for i, iso in enumerate(dates):
            add(iso, "Credito", mov_type, produto(ticker), ITAU, float(held), "-",
                round(base * (1 + 0.037 * i), 2))

    for ticker, first, last, base, held in FII_INCOME:
        for i, ym in enumerate(months(first, last)):
            add(f"{ym}-14", "Credito", "Rendimento", produto(ticker), ITAU,
                float(held), "-", round(base * (1 + 0.0055 * i), 2))

    for iso, direction, mov_type, ticker, qty, price, valor in MISC_EVENTS:
        prod = produto(ticker) if ticker in NAME else f"{ticker} - XP INC BDR"
        add(iso, direction, mov_type, prod, ITAU, float(qty), price, valor)

    return rows


def build_renda_fixa() -> list[list]:
    return [
        [iso, direction, br(iso), mov_type, prod, XPI, float(qty), price, valor]
        for iso, direction, mov_type, prod, qty, price, valor in RENDA_FIXA
    ]


def write_xlsx(path: Path, cols: list[str], rows: list[list], sheet: str) -> None:
    pd.DataFrame(rows, columns=cols).to_excel(
        path, sheet_name=sheet, index=False, engine="openpyxl")


def main() -> None:
    spot, deriv = build_negociacao()
    mov = build_movimentacao()
    rf = build_renda_fixa()

    def neg_between(lo, hi):
        return [r for r, t in zip(spot, TRADES) if in_range(t[0], lo, hi)]

    def mov_between(lo, hi):
        return [r[1:] for r in mov if in_range(r[0], lo, hi)]

    # 16 files, same names B3 produced. Overlapping windows are deliberate:
    # re-exported periods are how the dedup path gets exercised.
    files = [
        ("negociacao-2026-05-08-17-48-45.xlsx", NEG_COLS, neg_between("2024-01-01", "2024-06-30"), "Negociação"),
        ("negociacao-2026-05-08-17-49-14.xlsx", NEG_COLS, neg_between("2024-07-01", "2024-12-31"), "Negociação"),
        ("negociacao-2026-05-08-17-49-33.xlsx", NEG_COLS, neg_between("2025-01-01", "2025-06-30"), "Negociação"),
        ("negociacao-2026-05-10-22-49-36.xlsx", NEG_COLS, neg_between("2025-07-01", "2025-12-31"), "Negociação"),
        ("negociacao-2026-05-10-22-49-53.xlsx", NEG_COLS, neg_between("2026-01-01", "2026-12-31"), "Negociação"),
        # re-export of the whole of 2025 → every 2025 trade arrives twice
        ("negociacao-2026-05-10-22-50-14.xlsx", NEG_COLS, neg_between("2025-01-01", "2025-12-31"), "Negociação"),
        ("negociacao-2026-05-10-22-50-41.xlsx", NEG_COLS, deriv, "Negociação"),
        # re-export of 2026 → duplicates again
        ("negociacao-2026-06-25-13-57-41.xlsx", NEG_COLS, neg_between("2026-01-01", "2026-12-31"), "Negociação"),
        ("movimentacao-2026-05-08-18-45-28.xlsx", MOV_COLS, mov_between("2024-01-01", "2024-06-30"), "Movimentação"),
        ("movimentacao-2026-05-08-18-46-11.xlsx", MOV_COLS, mov_between("2024-07-01", "2024-12-31"), "Movimentação"),
        ("movimentacao-2026-05-08-18-48-43.xlsx", MOV_COLS, mov_between("2025-01-01", "2025-06-30"), "Movimentação"),
        ("movimentacao-2026-05-10-22-47-45.xlsx", MOV_COLS, mov_between("2025-07-01", "2025-12-31"), "Movimentação"),
        ("movimentacao-2026-05-10-22-48-08.xlsx", MOV_COLS, mov_between("2026-01-01", "2026-12-31"), "Movimentação"),
        # re-export of 2025 H2 → duplicate cash events collapse on load
        ("movimentacao-2026-05-10-22-48-29.xlsx", MOV_COLS, mov_between("2025-07-01", "2025-12-31"), "Movimentação"),
        ("movimentacao-2026-05-10-22-49-05.xlsx", MOV_COLS, [r[1:] for r in rf], "Movimentação"),
        ("movimentacao-2026-06-25-13-57-05.xlsx", MOV_COLS, mov_between("2024-09-19", "2024-09-25"), "Movimentação"),
    ]

    IMPORTS.mkdir(parents=True, exist_ok=True)
    for old in IMPORTS.glob("*.xlsx"):
        old.unlink()
    for name, cols, rows, sheet in files:
        assert rows, f"{name} would be empty — fix the window"
        write_xlsx(IMPORTS / name, cols, rows, sheet)
        print(f"{name:44s} {len(rows):5d} rows")

    OVERRIDES_CSV.write_text(OVERRIDES_TEXT)
    data_lines = [l for l in OVERRIDES_TEXT.splitlines() if not l.startswith("#")]
    print(f"{OVERRIDES_CSV.name:44s} {len(data_lines) - 1:5d} rows")


if __name__ == "__main__":
    main()
