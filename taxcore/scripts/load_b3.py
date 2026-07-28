"""Parse B3 reports from b3_imports/ into a positions view + cash events.

The B3 investor portal (Área do Investidor) lets you download two relevant
reports under "Extrato":

    1. Negociação    — every trade (Compra / Venda), clean schema, equities
                       and FIIs only. The "what was bought/sold" view.
    2. Movimentação  — every account event: trades + dividendos + JCP +
                       rendimentos (FII distributions) + amortizações +
                       BTC lending entries + custody transfers. Verbose.

We accept either or both. File type is auto-detected from the column
structure. Combining them gives you:
  - a unified trade history (deduped across reports)
  - a cash-events table (dividends, JCP, FII rendimentos, amortizações)
  - positions with cost basis adjusted for FII amortizações

Negociação schema (sheet 'Negociação'):
    Data do Negócio | Tipo de Movimentação | Mercado | Prazo/Vencimento |
    Instituição | Código de Negociação | Quantidade | Preço | Valor

Movimentação schema:
    Entrada/Saída | Data | Movimentação | Produto | Instituição |
    Quantidade | Preço unitário | Valor da Operação

Outputs:
    - data/positions.csv      (current holdings, weighted-avg cost,
                              amortization- and split-adjusted)
    - data/trades.csv         (deduped trades from both report types)
    - data/cash_events.csv    (dividendos, JCP, rendimentos, amortizações,
                              reembolsos, qty events like Desdobro/Bonificação)
    - stdout JSON summary

Notes on event handling:
    - Desdobro / Bonificação em Ativos / Fração em Ativos are qty-only events
      (stock split, bonus shares, fractional adjustment). They change the
      position's quantity without altering total cost basis — avg_cost falls
      (split/bonus) or rises (fração debit) accordingly. They are recorded in
      cash_events.csv with event_category == "qty_event" and applied
      chronologically alongside trades inside derive_positions().
    - Option activity ("Opção de Compra/Venda sobre Ações" and "Exercício de
      Opção de *" in Negociação) is handled by process_option_activity():
        • Premium trades (open/close of options without assignment) become
          cash_events rows with event_category == "premium" — sells are +cash,
          buys are -cash. Ticker is the 4-letter underlying root (e.g. ITSA).
        • Exercise/assignment rows are rewritten as trades on the underlying
          ticker (e.g. B3SAW130E Compra 700 @ 12.95 → B3SA3 Compra 700 @ 12.95).
          The underlying ticker is resolved by matching qty+price+date against
          the Movimentação "Transferência - Liquidação" row that booked the
          settlement, since Negociação records exercises under the option-
          series code and only Movimentação carries the real underlying.
    - "Empréstimo" (BTC stock-lending) entries are NOT classified as income.
      They are treated as skip rows even though some carry small fee values,
      because their schema mixes lending-flow bookkeeping with the actual
      fee credit. IVVB11 in particular has no Dividendo / Rendimento events
      in the data — it is iShares S&P 500 (an accumulation ETF that does
      not distribute). Lending fees are real cash but not reportable as
      "investment income" of the dividend/JCP/rendimento class, so we leave
      them out rather than fabricate distributions.
    - "Reembolso" (broker / withholding refunds) is captured but tagged as
      event_category == "reembolso" so portfolio_status.py reports it on a
      separate line and does NOT roll it into income totals.

Usage:
    python -m scripts.load_b3
    python -m scripts.load_b3 --since 2024-01-01
    python -m scripts.load_b3 --all-markets   # include Renda Fixa, etc.
    python -m scripts.load_b3 --no-consolidate  # keep *F split from round-lot
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
IMPORTS_DIR = ROOT / "b3_imports"
DATA_DIR = ROOT / "data"
POSITIONS_CSV = DATA_DIR / "positions.csv"
TRADES_CSV = DATA_DIR / "trades.csv"
CASH_EVENTS_CSV = DATA_DIR / "cash_events.csv"
RF_MARKS_CSV = DATA_DIR / "rf_marks.csv"
OVERRIDES_CSV = DATA_DIR / "cost_basis_overrides.csv"


# Ticker renames: B3 keeps trades/events under the OLD code and continues the
# position under the NEW one without a corporate-action event, so the new
# ticker shows orphan sells (oversold) while the old one lingers as a phantom
# holding — and downstream price lookups (yfinance) 404 on the dead code.
# Mapping OLD root -> NEW root here, applied at source, makes positions.csv /
# trades.csv / cash_events.csv all canonical, so every downstream script
# (portfolio_status, fetch_external_prices, sector_exposure, irpf_2026) sees
# one ticker. Add entries as renames happen. (ISA CTEEP / Transmissão Paulista
# renamed TRPL3/4 -> ISAE3/4 in 2024.)
TICKER_RENAMES: dict[str, str] = {
    "TRPL3": "ISAE3",
    "TRPL4": "ISAE4",
}


# A fractional-market ticker is the round-lot code plus an F (VTRU3F -> VTRU3).
# Anchored on the round-lot shape, not on the bare suffix: a renda fixa contract
# code ending in F ("LCI - LCI24H9RKF") would otherwise be silently truncated
# here, again in consolidate_fractional, and again in derive_positions — at
# different stages of the pipeline, so the three would disagree about one book.
_FRACTIONAL_RE = r"^([A-Z][A-Z0-9]{3}\d{1,2})F$"
_ROUND_LOT = r"\1"


def apply_ticker_renames(df: pd.DataFrame) -> pd.DataFrame:
    """Rewrite old ticker roots to their current code, preserving any *F suffix
    (TRPL4F -> ISAE4F) so fractional consolidation still works downstream."""
    if df.empty or "ticker" not in df.columns:
        return df
    df = df.copy()
    t = df["ticker"].astype(str)
    frac = t.str.match(_FRACTIONAL_RE)
    root = t.str.replace(_FRACTIONAL_RE, _ROUND_LOT, regex=True).replace(TICKER_RENAMES)
    df["ticker"] = root.where(~frac, root + "F")
    return df


# Map B3's column names to internal snake_case names.
COLUMN_MAP = {
    "Data do Negócio":         "date",
    "Tipo de Movimentação":    "side",
    "Mercado":                 "market",
    "Prazo/Vencimento":        "maturity",
    "Instituição":             "broker",
    "Código de Negociação":    "ticker",
    "Quantidade":              "qty",
    "Preço":                   "price",
    "Valor":                   "amount",
}


# ---------------------------------------------------------------------------
# Movimentação support
# ---------------------------------------------------------------------------

# Row-level classifier for Movimentação. Each Movimentação string maps to one
# of several categories so the main parser knows what to do with the row:
#   - "trade"     : produces a trade row (same shape as Negociação)
#   - "cash"      : produces a cash_events row (dividend / JCP / rendimento)
#   - "reembolso" : cash event but NOT investment income (broker refunds, etc.)
#   - "amort"     : cash event AND reduces cost basis at the security level
#   - "qty_event" : changes position quantity without changing total cost
#                   (Desdobro = stock split, Bonificação em Ativos = bonus
#                    shares, Fração em Ativos = fractional adjustment)
#   - "other"     : recorded in cash_events for visibility but not summed as
#                   income (Leilão de Fração, PAGAMENTO DE JUROS).
#   - "skip"      : ignore (BTC lending, custody admin, cancelled events, etc.)
# Anything not listed defaults to "skip" so we never silently miscategorize.
MOV_CATEGORY = {
    # --- Trades (settlement of an exchange order) ---
    "Transferência - Liquidação": "trade",
    "Compra": "trade",
    "Venda": "trade",
    "COMPRA / VENDA": "trade",
    "COMPRA/VENDA DEFINITIVA/CESSAO": "trade",
    "MDA COMPRA/VENDA DEFINITIVA MERCADO PRIMARIO": "trade",
    # --- Cash income (dividends and equivalents only) ---
    "Dividendo":                   "cash",
    "Juros Sobre Capital Próprio": "cash",
    "Rendimento":                  "cash",   # FII / CRI monthly distribution
    # --- Reembolso: broker / tax-withholding refunds, NOT investment income ---
    "Reembolso":                   "reembolso",
    # --- Other cash flows we keep visible but do not call income ---
    "Leilão de Fração":            "other",
    "PAGAMENTO DE JUROS":          "other",
    # --- Amortizations: principal returned to holder, reduces cost basis ---
    "Amortização": "amort",
    "AMORTIZAÇÃO": "amort",
    # --- Quantity-only corporate actions (no cash, no cost-basis change) ---
    "Desdobro":              "qty_event",
    "Bonificação em Ativos": "qty_event",
    "Fração em Ativos":      "qty_event",
    # --- Renda fixa contract lifecycle (CDB, LCI, LCA, CRI, CRA, debêntures,
    # Tesouro Direto). Aplicação is the buy, resgate/vencimento the sell — the
    # same shape as a trade, so they need no special case downstream. Same
    # labels irpf_2026.renda_fixa_year_end already recognises; keep in step.
    "APLICAÇÃO":                        "trade",
    "APLICAÇÃO/DEPÓSITO":               "trade",
    "VENCIMENTO":                       "trade",
    "VENCIMENTO/RESGATE SALDO EM CONTA": "trade",
    "RESGATE ANTECIPADO":               "trade",
    "Resgate":                          "trade",
    # RETIRADA DE CUSTÓDIA is two unrelated events under one label and is
    # resolved per row below, not here.
}


# B3 ticker shape: first char is a letter, then 3 alphanumeric, then 1-2 digits.
# Allows embedded digits in the prefix (e.g. B3SA3, M1TA34) which a strict
# [A-Z]{4} rule would wrongly reject.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}$")


# Renda fixa products, which B3 books as 'PREFIX - <contrato> - <emissor>'
# ('CDB - CDB24C7HQ2L - BANCO ORIGINAL S/A') or, for Tesouro Direto, as a plain
# name ('Tesouro Selic 2029'). Case-insensitive because B3 writes the bank paper
# in caps and the Tesouro titles in mixed case. Same prefix list as
# irpf_2026.renda_fixa_year_end — copied rather than imported, like _TICKER_RE
# is across this repo, because the two run in different processes. Keep in step.
_RF_PREFIX_RE = re.compile(
    r"^(CDB|LCI|LCA|LC|LF|CRA|CRI|TESOURO|DEB|NTN|LTN|LFT|RDB)\b", re.IGNORECASE)

# B3's own label for the market these are booked under. Shared by listed
# fixed-income ETFs (LFTB11), which is why the ticker decides, never the label.
RENDA_FIXA_MARKET = "Renda Fixa"


def _rf_code(produto: str) -> str | None:
    """Stable identity for a renda fixa 'Produto' cell, or None if it isn't one.

    'PREFIX - <contrato>' — the first two segments only. The issuer is the third
    and is left out on purpose: it reformats between reports ('BANCO PINE S/A'
    vs 'BANCO PINE S.A.') and would split one contract into two holdings.
    irpf_2026 still reads it off the raw extract, where it needs the CNPJ lookup.
    """
    if not isinstance(produto, str) or not _RF_PREFIX_RE.match(produto.strip()):
        return None
    parts = [p.strip() for p in produto.split(" - ") if p.strip()]
    return " - ".join(parts[:2]).upper()


def _extract_ticker(produto: str) -> str | None:
    """Extract the security identity from a Movimentação 'Produto' cell.

    Two forms: 'TICKER - COMPANY NAME' for exchange-traded names, and
    'CRI - <code> - <issuer>' / 'CDB - ...' for renda fixa contracts, which have
    no listed ticker and are identified by their contract code instead. Anything
    else (e.g. 'CFF - ...') stays None and its rows are dropped, as before.
    """
    if not isinstance(produto, str):
        return None
    head = produto.split(" - ", 1)[0].strip().upper()
    if _TICKER_RE.fullmatch(head):
        return head
    return _rf_code(produto)


def read_movimentacao(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse a Movimentação .xlsx into (trades, cash_events) DataFrames.

    The trade DataFrame uses the same internal schema as `read_negociacao`
    so it can be concatenated downstream. cash_events keeps a richer event
    type and is consumed both for amortization-adjusted cost basis and for
    future dividend/total-return analysis.
    """
    raw = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    raw["__source_file"] = path.name

    raw["date"] = pd.to_datetime(raw["Data"], dayfirst=True, errors="coerce")
    raw["ticker"] = raw["Produto"].map(_extract_ticker)
    raw["qty"] = pd.to_numeric(raw["Quantidade"], errors="coerce")
    raw["price"] = pd.to_numeric(raw["Preço unitário"], errors="coerce")
    raw["amount"] = pd.to_numeric(raw["Valor da Operação"], errors="coerce")
    raw["broker"] = raw["Instituição"].astype(str)
    raw["mov_type"] = raw["Movimentação"].astype(str).str.strip()
    raw["direction"] = raw["Entrada/Saída"].astype(str).str.strip().str.lower()
    raw["category"] = raw["mov_type"].map(MOV_CATEGORY).fillna("skip")
    raw["is_rf"] = raw["Produto"].map(_rf_code).notna()

    # RETIRADA DE CUSTÓDIA is two unrelated events sharing one label. With a
    # settlement value it is a transfer out that paid — a resgate. With none it
    # is custody frozen (issuer under intervention, FGC pending): the paper still
    # exists, is still declarable at cost, and booking it as a sale would report
    # a redemption that never happened. Mirrors irpf_2026.py's `frozen` branch.
    # Scoped to renda fixa. On an equity the same label is a custody transfer to
    # another broker: the shares move, nothing is sold, and booking it as a Venda
    # invents a realized gain that feeds the month's AÇÃO gross, the R$20k
    # isenção headroom and the DARF. It was `skip` before this branch existed,
    # and for equities it stays `skip`.
    held = (raw["mov_type"] == "RETIRADA DE CUSTÓDIA") & raw["is_rf"]
    raw.loc[held, "category"] = np.where(
        raw.loc[held, "amount"].fillna(0) > 0, "trade", "frozen")

    trade_rows = raw[raw["category"] == "trade"].copy()
    # Direction -> Compra/Venda; skip rows missing essential numeric fields.
    trade_rows["side"] = trade_rows["direction"].map(
        lambda d: "Compra" if d.startswith("credito") else "Venda")

    # Renda fixa is denominated inconsistently: the same R$25k CDB aplicação is
    # 2,500,000 units at PU 0.01 in one report and a CRI is 8 units at PU
    # 1035.42 in another. `Valor ≈ Quantidade × PU` holds under either, so derive
    # the PU from the ratio rather than trusting a convention, and fall back to
    # one unit priced at the whole amount when a report carries no quantity at
    # all. Equities keep B3's own Preço unitário untouched.
    rf = trade_rows["is_rf"]
    if rf.any():
        qty = trade_rows["qty"].where(trade_rows["qty"] > 0).fillna(1.0)
        pu = (trade_rows["amount"] / qty).where(trade_rows["amount"] > 0)
        trade_rows.loc[rf, "qty"] = qty[rf]
        trade_rows.loc[rf, "price"] = pu[rf].fillna(trade_rows.loc[rf, "price"])

    trade_rows = trade_rows.dropna(subset=["date", "ticker", "qty", "price"])
    trade_rows = trade_rows[(trade_rows["qty"] > 0) & (trade_rows["price"] > 0)]
    # Movimentação doesn't expose the market, so it is inferred: renda fixa from
    # the Produto shape, everything else spot.
    trade_rows["market"] = np.where(
        trade_rows["is_rf"], "Renda Fixa", "Mercado à Vista")
    trade_rows["maturity"] = "-"
    trade_rows["signed_qty"] = np.where(
        trade_rows["side"] == "Compra", trade_rows["qty"], -trade_rows["qty"])
    trades = trade_rows[[
        "date", "side", "market", "maturity", "broker", "ticker",
        "qty", "price", "amount", "signed_qty", "__source_file",
    ]]

    # Everything that's not a trade or skip becomes a cash_events row. That
    # bucket includes income, reembolso, amortization, qty_event and "other".
    # We tag it with event_category so downstream consumers (portfolio_status,
    # derive_positions) can filter precisely.
    # "frozen" carries no cash and never touches cost basis; it is kept so the
    # portfolio can say *why* a contract it still holds has no closing mark,
    # instead of listing it as merely unpriced.
    keep_categories = ("cash", "reembolso", "amort", "qty_event", "other",
                       "frozen")
    cash_rows = raw[raw["category"].isin(keep_categories)].copy()
    cash_rows = cash_rows.dropna(subset=["date", "ticker"])
    # event_type stays human-readable so users can grep cash_events.csv later.
    cash_rows["event_type"] = cash_rows["mov_type"]
    # Map internal category -> stable public label written to CSV.
    _label = {"cash": "income", "reembolso": "reembolso",
              "amort": "amortization", "qty_event": "qty_event",
              "other": "other", "frozen": "frozen"}
    cash_rows["event_category"] = cash_rows["category"].map(_label)
    # is_amortization stays for backwards compatibility with older code paths.
    cash_rows["is_amortization"] = cash_rows["category"] == "amort"
    # For qty_events, sign the quantity using Entrada/Saída: Credito = +,
    # Debito = -. The 'qty' field on the raw row is unsigned. Other event
    # rows leave qty as-is (unused by downstream consumers).
    qty_mask = cash_rows["category"] == "qty_event"
    if qty_mask.any():
        sign = cash_rows.loc[qty_mask, "direction"].map(
            lambda d: 1.0 if d.startswith("credito") else -1.0)
        cash_rows.loc[qty_mask, "qty"] = (
            cash_rows.loc[qty_mask, "qty"].astype(float) * sign)
        # qty_events have no monetary value; coerce nan/'-' to 0 for clean CSV.
        cash_rows.loc[qty_mask, "amount"] = 0.0

    cash_events = cash_rows[[
        "date", "ticker", "event_type", "event_category", "is_amortization",
        "qty", "price", "amount", "broker", "__source_file",
    ]]

    return trades, cash_events


def detect_file_type(path: Path) -> str:
    """Return 'negociacao', 'movimentacao' or 'posicao'.

    The first two are sniffed from the first sheet's columns. The posição report
    is a multi-sheet workbook whose first sheet ("Posição - Ações") matches
    neither, so it is recognised by the sheet it exists for instead.
    """
    with pd.ExcelFile(path, engine="openpyxl") as xl:
        if POSICAO_RF_SHEET in xl.sheet_names:
            return "posicao"
        cols = set(pd.read_excel(xl, sheet_name=0, nrows=0).columns)
    if {"Tipo de Movimentação", "Código de Negociação"}.issubset(cols):
        return "negociacao"
    if {"Movimentação", "Produto", "Entrada/Saída"}.issubset(cols):
        return "movimentacao"
    raise SystemExit(f"Could not classify {path.name}: columns = {sorted(cols)}")


# ---------------------------------------------------------------------------
# Posição support (relatório consolidado mensal) — marks for open contracts
# ---------------------------------------------------------------------------

POSICAO_RF_SHEET = "Posição - Renda Fixa"
# MTM first: it is what the paper would fetch today, where a secondary market
# exists to say so. CURVA is the contracted accrual, blind to rates having
# moved since — but it is the only number B3 publishes for bank paper (every
# CDB row carries MTM "-"), and for paper held to maturity it is also the right
# one. Order, not choice: whichever B3 filled in.
# ponytail: per-row fallback, not per-contract. If B3 ever fills MTM for a
# contract in one month's report and only CURVA in the next, that contract's two
# marks sit on different valuation bases and the rate implied between them is
# neither. Not seen in a real report — which column B3 populates follows the
# instrument, not the month. Record the column beside the price if it ever is.
_POSICAO_PRICE_COLS = ("Preço Atualizado MTM", "Preço Atualizado CURVA")

_MONTHS_PT = {name: n for n, name in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
     "agosto", "setembro", "outubro", "novembro", "dezembro"], start=1)}


def _posicao_date(name: str) -> dt.date:
    """The date a posição report describes: the close of its reference month.

    Nothing inside the workbook says. The renda fixa sheet has no reference-date
    column, and the Negociações sheet's "Período (Final)" is a literal "-". The
    filename B3 hands out is the only source, so a report renamed past
    recognition is refused rather than dated by guess — every mark in the file
    hangs off this one date, and a wrong one bends the implied rate of every
    contract in it.
    """
    stem = unicodedata.normalize("NFKD", Path(name).stem)
    stem = stem.encode("ascii", "ignore").decode().lower()
    # Searched anywhere in the name and not anchored to its end: a browser that
    # has seen the file before saves it as "...-2026-junho (1).xlsx", which is
    # the single most likely way a real upload arrives. Anchoring turned that
    # into a SystemExit, and a SystemExit here fails the tenant's whole rebuild.
    # Last valid (year, month) wins, so the trailing counter and any earlier
    # year-like run in the name are both stepped over rather than matched.
    found = [(int(y), _MONTHS_PT.get(tok, int(tok) if tok.isdigit() else 0))
             for y, tok in re.findall(r"(20\d{2})[-_]([a-z]+|\d{1,2})", stem)]
    found = [(y, m) for y, m in found if 1 <= m <= 12]
    if not found:
        raise SystemExit(
            f"Could not read the reference month of the posição report {name!r}. "
            "Keep B3's own filename (relatorio-consolidado-mensal-AAAA-mes.xlsx) "
            "— nothing inside the file says which month it covers.")
    year, month = found[-1]
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def read_posicao(path: Path) -> pd.DataFrame:
    """(date, ticker, price) marks from a relatório consolidado mensal.

    The third extract, and the only one that prices a contract while it is still
    held. Negociação and Movimentação report fills, so a CDB nobody has redeemed
    has exactly one price in the whole book — and one price implies no rate.
    `Preço Atualizado` is a per-unit number the issuer computed on the report
    date: a second mark, from which portfolio_index.accrue_rf derives the rate
    exactly. The indexer B3 does print here (`DI` / `IPCA` / `PREFIXADO`) is
    deliberately ignored — it carries neither the percentage nor the spread, so
    it cannot fill renda_fixa_terms, and with a second mark it does not need to.

    The sheet splits what Movimentação keeps in one cell — "CDB - BANCO AGIBANK
    S/A" in Produto, "CDB325AQWAM" in Código — so the two are recombined through
    `_rf_code`, which owns the contract name on both sides of the join. That
    also drops the rows this is not about: the "CFF -" FIP quotas, and the blank
    totals row the sheet ends on.
    """
    df = pd.read_excel(path, sheet_name=POSICAO_RF_SHEET, engine="openpyxl")
    have = [c for c in _POSICAO_PRICE_COLS if c in df.columns]
    if not have:
        raise SystemExit(
            f"{path.name}: sheet {POSICAO_RF_SHEET!r} has none of "
            f"{list(_POSICAO_PRICE_COLS)} — B3 changed the layout.")

    price = pd.to_numeric(df[have[0]], errors="coerce")
    for col in have[1:]:
        price = price.fillna(pd.to_numeric(df[col], errors="coerce"))

    out = pd.DataFrame({
        "date": _posicao_date(path.name),
        "ticker": [_rf_code(f"{str(p).split(' - ')[0]} - {str(c).strip()}")
                   for p, c in zip(df["Produto"], df["Código"])],
        # A zero or a blank is not a price. Kept as NaN and dropped below rather
        # than defaulted, because downstream it would read as a per-unit value of
        # zero and draw the contract to a total loss on the report date.
        "price": price.where(price > 0),
    })
    return out.dropna(subset=["ticker", "price"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Negociação support (the original, simpler reader)
# ---------------------------------------------------------------------------


def read_negociacao(path: Path) -> pd.DataFrame:
    """Read a single B3 Negociação .xlsx and return a normalized DataFrame."""
    # The Negociação report has the header row at row 1 (no banner). Try the
    # default first, then fall back to a couple of skiprows in case B3 ever
    # adds a header banner like Movimentação has.
    last_err: Exception | None = None
    for skip in (0, 1, 2):
        try:
            df = pd.read_excel(path, sheet_name=0, skiprows=skip, engine="openpyxl")
            if "Código de Negociação" in df.columns and "Tipo de Movimentação" in df.columns:
                df = df.rename(columns=COLUMN_MAP)
                df["__source_file"] = path.name
                return df
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise SystemExit(
        f"Could not parse {path.name} as a Negociação report. "
        f"Last error: {last_err}"
    )


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], dayfirst=True, errors="coerce")
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce")
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
    out["side"] = out["side"].astype(str).str.strip()
    out["market"] = out["market"].astype(str).str.strip()
    out["ticker"] = out["ticker"].astype(str).str.strip().str.upper()
    # Signed quantity: + for buys, - for sells
    out["signed_qty"] = np.where(
        out["side"].str.lower().str.startswith("compra"), out["qty"], -out["qty"])
    return out.dropna(subset=["date", "ticker", "qty", "price"])


# ---------------------------------------------------------------------------
# Option-activity handling
# ---------------------------------------------------------------------------
# B3 option series codes are <UNDERLYING_ROOT(4)><SERIES_LETTER><STRIKE_TAG>,
# with an optional trailing 'E' on exercise lines. e.g. B3SAW130E → root B3SA.
# Underlying roots may include embedded digits (B3SA, M1TA), so the first 4
# chars are letter + 3 alphanumeric.
_OPT_SERIES_RE = re.compile(r"^([A-Z][A-Z0-9]{3})[A-Z]\d+E?$")


def _option_underlying_root(code: str) -> str | None:
    if not isinstance(code, str):
        return None
    m = _OPT_SERIES_RE.match(code.strip().upper())
    return m.group(1) if m else None


def _resolve_exercise_underlying(
    exercises: pd.DataFrame,
    mov_settlements: pd.DataFrame,
    window_days: int = 5,
) -> tuple[dict, list]:
    """Match each Negociação exercise row to its Movimentação settlement row.

    Negociação records option exercises under the option-series code
    (e.g. 'B3SAW130E'); the underlying ticker only appears in the matching
    Movimentação 'Transferência - Liquidação' row, dated T+2-ish. We join by
    qty+price within a small date window and return {exercise_idx: underlying}.
    """
    if exercises.empty or mov_settlements.empty:
        return {}, list(exercises.index)

    s = mov_settlements.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.dropna(subset=["date", "ticker", "qty", "price"])

    resolved: dict = {}
    unresolved: list = []
    used: set = set()
    for ex_idx, ex in exercises.iterrows():
        ex_date = pd.to_datetime(ex["date"])
        ex_qty = float(ex["qty"])
        ex_price = float(ex["price"])
        day_delta = (s["date"] - ex_date).dt.days
        mask = (
            (~s.index.isin(used))
            & day_delta.between(0, window_days)
            & ((s["qty"] - ex_qty).abs() < 1e-3)
            & ((s["price"] - ex_price).abs() < 1e-4)
        )
        candidates = s[mask]
        if candidates.empty:
            unresolved.append(ex_idx)
            continue
        best = candidates.sort_values("date").iloc[0]
        resolved[ex_idx] = best["ticker"]
        used.add(best.name)
    return resolved, unresolved


def process_option_activity(
    neg_trades: pd.DataFrame,
    mov_settlements: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list, dict]:
    """Split Negociação trades into equity-only trades + premium cash events.

    Three input row classes are handled:
      • Mercado contains 'Opção' AND not 'Exercício'  → option premium trades.
        Each becomes a cash_events row with event_category='premium', ticker
        set to the 4-letter underlying root. Sells (+cash), buys (-cash).
      • Mercado starts with 'Exercício'  → assignment/exercise of an option.
        The row is rewritten as a trade on the resolved underlying ticker
        (Mercado à Vista), so it flows into derive_positions like a normal buy
        or sell. Unresolved exercises (no Movimentação match found) are
        dropped and reported back to the caller.
      • everything else → returned unchanged.

    Returns (clean_trades, premium_events, unresolved_details, stats).
    """
    empty_stats = {
        "exercises_resolved": 0,
        "exercises_unresolved": 0,
        "option_trades_processed": 0,
        "net_premium_brl": 0.0,
    }
    if neg_trades.empty:
        return neg_trades, pd.DataFrame(), [], empty_stats

    market = neg_trades["market"].fillna("").astype(str)
    is_exercise = market.str.startswith("Exercício")
    is_premium = market.str.contains("Opção") & ~is_exercise

    non_opt = neg_trades[~(is_exercise | is_premium)].copy()
    exercises = neg_trades[is_exercise].copy()
    premiums = neg_trades[is_premium].copy()

    # Resolve each exercise's underlying ticker via the Mov settlement row.
    resolved, _ = _resolve_exercise_underlying(exercises, mov_settlements)
    rewritten_rows = []
    unresolved_details: list = []
    for ex_idx, ex in exercises.iterrows():
        underlying = resolved.get(ex_idx)
        if underlying is None:
            unresolved_details.append({
                "date": str(pd.to_datetime(ex["date"]).date()),
                "code": ex["ticker"],
                "side": ex["side"],
                "qty": float(ex["qty"]),
                "price": float(ex["price"]),
                "market": ex["market"],
            })
            continue
        new = ex.copy()
        new["ticker"] = underlying
        new["market"] = "Mercado à Vista"
        new["maturity"] = "-"
        rewritten_rows.append(new)
    rewritten = (
        pd.DataFrame(rewritten_rows) if rewritten_rows else exercises.iloc[0:0]
    )
    clean_trades = (
        pd.concat([non_opt, rewritten], ignore_index=True)
        if not rewritten.empty else non_opt
    )

    # Build premium cash_events: one row per option open/close trade.
    # Ticker is the option-series code (e.g. 'CMIGI18'), not the underlying
    # root: distinct contracts on the same underlying often share date+qty+
    # amount and would collide under cash_events' dedup key. The series code
    # is unique per contract so true duplicates still collapse and real
    # distinct trades survive. Grep by 'startswith(\"CMIG\")' to roll up.
    premium_events = pd.DataFrame()
    if not premiums.empty:
        signed_amount = premiums["amount"].astype(float) * np.where(
            premiums["side"].astype(str).str.lower().str.startswith("venda"), 1.0, -1.0)
        premium_events = pd.DataFrame({
            "date":            premiums["date"].values,
            "ticker":          premiums["ticker"].astype(str).values,
            "event_type":      premiums["market"].values,
            "event_category":  "premium",
            "is_amortization": False,
            "qty":             premiums.get("signed_qty",
                                            premiums["qty"]).values,
            "price":           premiums["price"].values,
            "amount":          signed_amount.values,
            "broker":          premiums.get("broker", "").values
                               if "broker" in premiums else "",
            "__source_file":   premiums.get("__source_file", "").values
                               if "__source_file" in premiums else "",
        })

    stats = {
        "exercises_resolved": len(resolved),
        "exercises_unresolved": len(unresolved_details),
        "option_trades_processed": int(len(premiums)),
        "net_premium_brl": round(
            float(premium_events["amount"].sum())
            if not premium_events.empty else 0.0, 2),
    }
    return clean_trades, premium_events, unresolved_details, stats


def read_overrides(
    path: Path = OVERRIDES_CSV,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read user-maintained cost-basis overrides.

    The B3 reports cannot expose a price for shares that arrived via paths
    other than a market trade: IPO reservation deliveries record qty but
    leave Preço empty, spin-off Incorporação rows have no monetary value,
    transfers from another broker (Atualização) carry the qty only. The
    user fills in the missing prices/allocations in
    data/cost_basis_overrides.csv and we synthesize the events here.

    Returns (synthetic_trades, synthetic_cost_adjusts). Both DataFrames use
    the same internal schema as Negociação trades / cash_events so they
    concat cleanly with the rest of the pipeline.
    """
    empty_trades = pd.DataFrame(columns=[
        "date", "side", "market", "maturity", "broker", "ticker",
        "qty", "price", "amount", "signed_qty", "__source_file",
    ])
    empty_adjusts = pd.DataFrame(columns=[
        "date", "ticker", "event_type", "event_category", "is_amortization",
        "qty", "price", "amount", "broker", "__source_file",
    ])
    if not path.exists():
        return empty_trades, empty_adjusts

    raw = pd.read_csv(path, comment="#")
    if raw.empty:
        return empty_trades, empty_adjusts
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["date", "ticker", "kind"])
    raw["kind"] = raw["kind"].astype(str).str.strip().str.lower()

    trade_rows = raw[raw["kind"].isin(("buy", "sell"))].copy()
    if not trade_rows.empty:
        trade_rows["side"] = trade_rows["kind"].map(
            {"buy": "Compra", "sell": "Venda"})
        trade_rows["qty"] = pd.to_numeric(trade_rows["qty"], errors="coerce")
        trade_rows["price"] = pd.to_numeric(trade_rows["price"], errors="coerce")
        trade_rows = trade_rows.dropna(subset=["qty", "price"])
        trade_rows["amount"] = (trade_rows["qty"] * trade_rows["price"]).round(2)
        trade_rows["market"] = "Mercado à Vista"
        trade_rows["maturity"] = "-"
        trade_rows["broker"] = "override"
        trade_rows["__source_file"] = path.name
        trade_rows["signed_qty"] = np.where(
            trade_rows["side"] == "Compra", trade_rows["qty"], -trade_rows["qty"])
        synthetic_trades = trade_rows[[
            "date", "side", "market", "maturity", "broker", "ticker",
            "qty", "price", "amount", "signed_qty", "__source_file",
        ]]
    else:
        synthetic_trades = empty_trades

    adjust_rows = raw[raw["kind"] == "cost_adjust"].copy()
    if not adjust_rows.empty:
        adjust_rows["amount"] = pd.to_numeric(adjust_rows["amount"], errors="coerce")
        adjust_rows = adjust_rows.dropna(subset=["amount"])
        adjust_rows["event_type"] = "Cost Adjustment (override)"
        adjust_rows["event_category"] = "amortization"
        adjust_rows["is_amortization"] = True
        adjust_rows["qty"] = 0.0
        adjust_rows["price"] = 0.0
        adjust_rows["broker"] = "override"
        adjust_rows["__source_file"] = path.name
        synthetic_adjusts = adjust_rows[[
            "date", "ticker", "event_type", "event_category", "is_amortization",
            "qty", "price", "amount", "broker", "__source_file",
        ]]
    else:
        synthetic_adjusts = empty_adjusts

    return synthetic_trades, synthetic_adjusts


def consolidate_fractional(positions: pd.DataFrame) -> pd.DataFrame:
    """Merge fractional-market positions (*F suffix) with their round-lot parent.

    B3 lists fractional and round-lot orders as different markets ("Mercado
    Fracionário" vs "Mercado à Vista"), and the ticker carries an 'F' suffix
    in the fractional market (VTRU3F vs VTRU3). They're the same security
    economically — only the lot size differs — so combining them is the
    correct view for "what do I own".

    Combined position uses cost-weighted average price across both lots,
    consistent with Brazilian PM (preço médio) accounting.
    """
    if positions.empty:
        return positions
    out = positions.copy()
    # Group key: round-lot ticker (strip trailing F if present and the parent exists)
    out["_canonical"] = out["ticker"].str.replace(
        _FRACTIONAL_RE, _ROUND_LOT, regex=True)
    grouped = (
        out.groupby("_canonical", as_index=False)
        .agg(
            qty=("qty", "sum"),
            total_cost=("total_cost", "sum"),
            # If a position spans both markets, "Mercado à Vista" wins for display.
            market=("market", lambda s: "Mercado à Vista" if "Mercado à Vista" in s.values else s.iloc[0]),
        )
        .rename(columns={"_canonical": "ticker"})
    )
    grouped["avg_cost"] = (grouped["total_cost"] / grouped["qty"]).round(4)
    grouped["total_cost"] = grouped["total_cost"].round(2)
    grouped["qty"] = grouped["qty"].round(6)
    return grouped[["ticker", "qty", "avg_cost", "total_cost", "market"]] \
        .sort_values("ticker").reset_index(drop=True)


def derive_positions(
    trades: pd.DataFrame,
    cash_events: pd.DataFrame | None = None,
    consolidate: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    """Reduce trades + corporate-action events into current holdings.

    Uses average-cost accounting (preço médio):
      - Buys add (qty * price) to cost basis and increase qty.
      - Sells reduce cost basis by (sold_qty * current_avg_cost), preserving
        the running avg. Brazilian Receita uses this same convention for IR.
      - Amortizations (Amortização / FII or CRI principal returns) reduce
        cost basis by the amount received; qty is unchanged.
      - qty_events (Desdobro split, Bonificação bonus, Fração adjustment)
        change qty without changing cost; avg_cost = cost / new_qty.

    When `consolidate=True`, fractional-market tickers (*F suffix) are merged
    with their round-lot parent before the chronological walk — they are the
    same security, only the lot size differs, and a split or bonus issued on
    the parent applies equally to fractional holdings.

    Returns (positions_df, tickers_with_incomplete_history). The second
    element is non-empty when the loaded data shows a sell with no prior
    buy — meaning earlier trades exist outside the report's date range, so
    the cost basis for that ticker is unreliable.
    """
    cols = ["ticker", "qty", "avg_cost", "total_cost", "market"]
    if trades.empty:
        return pd.DataFrame(columns=cols), []

    t = trades.copy()
    if consolidate:
        # Strip *F suffix so fractional and round-lot trades roll up to the
        # canonical ticker. We retain the most informative market label
        # ("Mercado à Vista" wins) for display per consolidate_fractional().
        t["ticker"] = t["ticker"].astype(str).str.replace(
            _FRACTIONAL_RE, _ROUND_LOT, regex=True)

    # Build a unified event timeline. Each row carries:
    #   kind ∈ {trade, qty_event, amort}
    #   sq    = signed quantity change (buys+, sells-, qty_event signed already)
    #   price = trade price (only used for kind=trade buys)
    #   amount = cash amount (only used for kind=amort, deducted from cost)
    rows: list[dict] = []
    for _, r in t.sort_values("date").iterrows():
        rows.append({
            "ticker": r["ticker"],
            "date":   pd.to_datetime(r["date"]),
            "kind":   "trade",
            "sq":     float(r["signed_qty"]),
            "price":  float(r["price"]),
            "amount": 0.0,
            "market": r["market"],
        })

    if cash_events is not None and not cash_events.empty:
        ce = cash_events.copy()
        if consolidate:
            ce["ticker"] = ce["ticker"].astype(str).str.replace(
                _FRACTIONAL_RE, _ROUND_LOT, regex=True)
        # qty_event rows: signed qty already populated by read_movimentacao.
        cat_col = ce["event_category"] if "event_category" in ce.columns else None
        if cat_col is not None:
            qty_evs = ce[cat_col == "qty_event"]
            amort_evs = ce[cat_col == "amortization"]
        else:
            # Backwards compat: older cash_events.csv without event_category.
            qty_evs = ce.iloc[0:0]
            amort_evs = ce[ce.get("is_amortization", pd.Series(False, index=ce.index)).fillna(False)]
        for _, r in qty_evs.iterrows():
            rows.append({
                "ticker": r["ticker"],
                "date":   pd.to_datetime(r["date"]),
                "kind":   "qty_event",
                "sq":     float(r["qty"]) if pd.notna(r["qty"]) else 0.0,
                "price":  0.0,
                "amount": 0.0,
                "market": None,
            })
        for _, r in amort_evs.iterrows():
            rows.append({
                "ticker": r["ticker"],
                "date":   pd.to_datetime(r["date"]),
                "kind":   "amort",
                "sq":     0.0,
                "price":  0.0,
                "amount": float(r["amount"]) if pd.notna(r["amount"]) else 0.0,
                "market": None,
            })

    timeline = pd.DataFrame(rows).dropna(subset=["date", "ticker"])
    if timeline.empty:
        return pd.DataFrame(columns=cols), []
    # Within a single date, apply trades first, then qty_events, then amorts
    # (so a split applies to post-trade qty, and an amort applies after).
    kind_order = {"trade": 0, "qty_event": 1, "amort": 2}
    timeline["__order"] = timeline["kind"].map(kind_order)
    timeline = timeline.sort_values(["ticker", "date", "__order"]).reset_index(drop=True)

    out_rows: list[dict] = []
    incomplete: list[str] = []
    for ticker, group in timeline.groupby("ticker"):
        qty = 0.0
        cost = 0.0
        market = None
        had_orphan_sell = False
        for _, ev in group.iterrows():
            kind = ev["kind"]
            if kind == "trade":
                sq = ev["sq"]
                price = ev["price"]
                if market is None and ev["market"] is not None:
                    market = ev["market"]
                elif ev["market"] == "Mercado à Vista":
                    # Prefer the round-lot label for display.
                    market = "Mercado à Vista"
                if sq > 0:
                    cost += sq * price
                    qty += sq
                else:
                    if qty > 0:
                        avg = cost / qty
                        cost += sq * avg  # sq < 0
                        qty += sq
                    else:
                        had_orphan_sell = True
            elif kind == "qty_event":
                # Quantity-only corporate action. Cost basis is preserved;
                # avg_cost shifts implicitly via cost / new_qty. If we have
                # no inventory at the time of the event, the event came in
                # for a position that was opened/closed outside this data —
                # treat as orphan and skip rather than going negative.
                if qty + ev["sq"] > 1e-9:
                    qty += ev["sq"]
                elif qty == 0 and ev["sq"] > 0:
                    # Bonus/split arriving without prior inventory: ignore
                    # (no cost basis to attribute it to).
                    had_orphan_sell = True
                # Else: a debit larger than current qty → flag and clamp.
                else:
                    had_orphan_sell = True
                    qty = max(0.0, qty + ev["sq"])
            elif kind == "amort":
                # Amortization returns principal: drop cost basis, qty stays.
                cost = max(0.0, cost - ev["amount"])
        if had_orphan_sell:
            incomplete.append(ticker)
        if qty > 1e-6:
            out_rows.append({
                "ticker": ticker,
                "qty": round(qty, 6),
                "avg_cost": round(cost / qty, 4),
                "total_cost": round(cost, 2),
                "market": market or "Mercado à Vista",
            })
    df = (
        pd.DataFrame(out_rows).sort_values("ticker").reset_index(drop=True)
        if out_rows else pd.DataFrame(columns=cols)
    )
    return df, incomplete


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, help="ignore trades before YYYY-MM-DD")
    p.add_argument("--all-markets", action="store_true",
                   help="keep all markets (default: Renda Variável only)")
    p.add_argument("--no-consolidate", action="store_true",
                   help="keep fractional (*F) and round-lot positions split "
                        "(default: merge them — they're the same security)")
    args = p.parse_args(argv[1:])

    files = sorted(IMPORTS_DIR.glob("*.xlsx"))
    if not files:
        print(f"No .xlsx files in {IMPORTS_DIR}/. "
              f"Drop your B3 Negociação or Movimentação reports there.",
              file=sys.stderr)
        return 1

    # Route each file to the matching parser. Negociação produces trades only;
    # Movimentação produces both trades and cash events.
    neg_dfs: list[pd.DataFrame] = []
    mov_trade_dfs: list[pd.DataFrame] = []
    cash_dfs: list[pd.DataFrame] = []
    posicao_dfs: list[pd.DataFrame] = []
    file_kinds: dict[str, str] = {}
    for f in files:
        kind = detect_file_type(f)
        file_kinds[f.name] = kind
        if kind == "negociacao":
            neg_dfs.append(read_negociacao(f))
        elif kind == "posicao":
            posicao_dfs.append(read_posicao(f))
        else:
            mt, ce = read_movimentacao(f)
            mov_trade_dfs.append(mt)
            cash_dfs.append(ce)

    # ---- Trades: prefer Negociação when available ----
    # Negociação stores trades on the trade date (the actual order execution).
    # Movimentação's "Transferência - Liquidação" rows are SETTLEMENT entries
    # for the same trades, recorded on T+2 with rolled-up quantities. They
    # would double-count if combined naively, so we only fall back to
    # Movimentação trades when no Negociação file is present.
    used_movimentacao_for_trades = False
    opt_stats: dict = {
        "exercises_resolved": 0, "exercises_unresolved": 0,
        "option_trades_processed": 0, "net_premium_brl": 0.0,
    }
    premium_events = pd.DataFrame()
    unresolved_exercises: list = []
    if neg_dfs:
        trades = normalize(pd.concat(neg_dfs, ignore_index=True))
        # Rewrite option exercises onto their underlying ticker and pull
        # premium activity out of trades into cash_events. We feed the
        # function Mov-side trade rows (which include "Transferência -
        # Liquidação" settlements) so it can resolve each exercise's
        # underlying by qty+price+date.
        mov_settlements = (
            pd.concat(mov_trade_dfs, ignore_index=True)
            if mov_trade_dfs else pd.DataFrame()
        )
        trades, premium_events, unresolved_exercises, opt_stats = (
            process_option_activity(trades, mov_settlements)
        )
        # Negociação is the exchange's own trade log, so it wins wherever the
        # two reports describe the same event — that is what this precedence
        # rule is for, since Movimentação's settlement rows are dupes of it.
        # Renda fixa contracts are *registered* at B3, never traded on it, so
        # they appear in no Negociação report at all: there is nothing for them
        # to duplicate, and dropping them with the rest of the Movimentação
        # trade rows loses every CDB, LCI and Tesouro aplicação the moment a
        # single Negociação file is present. Unioned back in; the dedup below
        # collapses the copies that overlapping report ranges produce.
        if not mov_settlements.empty:
            rf_rows = mov_settlements[
                mov_settlements["market"] == RENDA_FIXA_MARKET]
            if not rf_rows.empty:
                trades = pd.concat([trades, rf_rows], ignore_index=True)
    elif mov_trade_dfs:
        trades = pd.concat(mov_trade_dfs, ignore_index=True).dropna(
            subset=["date", "ticker"])
        used_movimentacao_for_trades = True
    else:
        trades = pd.DataFrame()

    # Dedup within the chosen source (overlapping report ranges still produce
    # exact duplicates that should collapse). Broker excluded so identical
    # trades with different broker formatting still match.
    before = len(trades)
    if not trades.empty:
        trades = trades.drop_duplicates(
            subset=["date", "side", "ticker", "qty", "price"]
        )
    deduped = before - len(trades)

    # ---- User-maintained cost-basis overrides ----
    # These backfill events the B3 report cannot expose (IPO reservation
    # prices, spin-off allocations, transfers from another broker). They are
    # added AFTER dedup so they cannot collapse against any genuine trade.
    override_trades, override_adjusts = read_overrides()
    if not override_trades.empty:
        trades = pd.concat([trades, override_trades], ignore_index=True)

    # Canonicalize renamed tickers (e.g. TRPL4 -> ISAE4) before splitting into
    # trades_for_positions and before deriving positions, so old- and new-coded
    # rows roll up as one security everywhere.
    trades = apply_ticker_renames(trades)

    if args.since:
        cutoff = datetime.fromisoformat(args.since)
        trades = trades[trades["date"] >= cutoff]

    if not args.all_markets:
        equity_pattern = r"Variável|à Vista|Vista|Fracionário"
        is_equity_market = trades["market"].str.contains(
            equity_pattern, case=False, na=False, regex=True)
        # Also include Renda Fixa ETFs (LFTB11 = Tesouro Selic, IMAB11 = IPCA
        # treasuries, B5P211, IRFM11, etc). These trade on B3 like stocks but
        # are recorded under Mercado = "Renda Fixa" because of their underlying.
        # Detected by: market contains "Renda Fixa" AND ticker matches the
        # standard B3 stock-ticker shape (rejects CDB/LCI/LCA contract codes
        # which are alphanumeric strings without the ticker pattern).
        is_renda_fixa = trades["market"].fillna("").astype(str).str.contains(
            "Renda Fixa", case=False, na=False)
        is_rf_etf = is_renda_fixa & trades["ticker"].astype(str).str.match(
            _TICKER_RE.pattern, na=False)
        # The contracts themselves — CDB, LCI, CRI, Tesouro Direto — are real
        # holdings too, identified by their contract code rather than a ticker.
        # Admitted by name here rather than by flipping --all-markets, which
        # would also let in Mercado = "Futuro": WIN/WDO daytrades net to zero
        # every session, so they are not a position, and nothing downstream of
        # positions.csv filters them back out.
        is_rf_contract = is_renda_fixa & trades["ticker"].astype(str).str.match(
            _RF_PREFIX_RE.pattern, case=False, na=False)
        trades_for_positions = trades[
            is_equity_market | is_rf_etf | is_rf_contract]
    else:
        trades_for_positions = trades

    # ---- Cash events: combine, dedup, save ----
    cash_events = (
        pd.concat(cash_dfs, ignore_index=True) if cash_dfs else pd.DataFrame(
            columns=["date", "ticker", "event_type", "event_category",
                     "is_amortization", "qty", "price", "amount", "broker",
                     "__source_file"])
    )
    if not premium_events.empty:
        cash_events = pd.concat([cash_events, premium_events], ignore_index=True)
    if not override_adjusts.empty:
        cash_events = pd.concat([cash_events, override_adjusts], ignore_index=True)
    if not cash_events.empty:
        cash_events = cash_events.drop_duplicates(
            subset=["date", "ticker", "event_type", "amount", "qty"]
        ).sort_values("date")
    cash_events = apply_ticker_renames(cash_events)

    # Marks from the posição reports. Kept out of trades and cash_events on
    # purpose: this is a price observation, not an event — nothing was bought,
    # sold or paid. Booking it as a zero-quantity trade would have saved a CSV
    # and then shown up in the trade list, the apuração and every count.
    # One row per (report month, contract); a month uploaded twice keeps the
    # later read of it.
    rf_marks = (pd.concat(posicao_dfs, ignore_index=True) if posicao_dfs
                else pd.DataFrame(columns=["date", "ticker", "price"]))
    if not rf_marks.empty:
        rf_marks = (rf_marks.drop_duplicates(subset=["date", "ticker"], keep="last")
                    .sort_values(["ticker", "date"]))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(TRADES_CSV, index=False)
    cash_events.to_csv(CASH_EVENTS_CSV, index=False)
    rf_marks.to_csv(RF_MARKS_CSV, index=False)

    # derive_positions handles trades, *F consolidation, qty events (splits /
    # bonificações / fracionários) and amortizations in a single chronological
    # walk. This is more accurate than the old "trades only, then post-process"
    # pipeline because mid-history splits adjust avg cost before any later
    # sell uses it.
    positions, incomplete = derive_positions(
        trades_for_positions,
        cash_events=cash_events,
        consolidate=not args.no_consolidate,
    )
    positions.to_csv(POSITIONS_CSV, index=False)

    # Summarize cash events by type for the JSON summary.
    cash_summary: dict[str, dict] = {}
    if not cash_events.empty:
        for ev, group in cash_events.groupby("event_type"):
            cash_summary[ev] = {
                "count": int(len(group)),
                "total_amount": round(float(group["amount"].sum()), 2),
            }

    summary = {
        "files_loaded": [
            {"name": f.name, "kind": file_kinds[f.name]} for f in files
        ],
        "trade_source": "movimentacao" if used_movimentacao_for_trades else "negociacao",
        "trades_total": len(trades),
        "trades_used_for_positions": len(trades_for_positions),
        "duplicates_removed": deduped,
        "markets_seen": sorted(trades["market"].dropna().unique().tolist())
                        if not trades.empty else [],
        "cash_events_total": int(len(cash_events)),
        "cash_events_by_type": cash_summary,
        "rf_marks_total": int(len(rf_marks)),
        "incomplete_history": incomplete,
        "option_activity": opt_stats,
        "unresolved_exercises": unresolved_exercises,
        "positions": positions.to_dict(orient="records"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    if incomplete:
        print(
            f"\n⚠️  Incomplete history for: {', '.join(incomplete)}. "
            f"These tickers have sells with no matching prior buys in the loaded data — "
            f"download an earlier-dated report to fix the cost basis.",
            file=sys.stderr,
        )
    if unresolved_exercises:
        print(
            f"\n⚠️  {len(unresolved_exercises)} option exercise(s) could not be "
            f"matched to a Movimentação settlement row — the underlying ticker "
            f"is unknown so the shares are missing from positions. "
            f"Re-download Movimentação covering the settlement date "
            f"(T+2 from the exercise date) and re-run.",
            file=sys.stderr,
        )
    print(
        f"\n→ Wrote {TRADES_CSV.relative_to(ROOT)} ({len(trades)} trades), "
        f"{CASH_EVENTS_CSV.relative_to(ROOT)} ({len(cash_events)} events), "
        f"and {POSITIONS_CSV.relative_to(ROOT)} ({len(positions)} positions)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
