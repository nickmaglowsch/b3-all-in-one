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
import json
import re
import sys
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


def apply_ticker_renames(df: pd.DataFrame) -> pd.DataFrame:
    """Rewrite old ticker roots to their current code, preserving any *F suffix
    (TRPL4F -> ISAE4F) so fractional consolidation still works downstream."""
    if df.empty or "ticker" not in df.columns:
        return df
    df = df.copy()
    t = df["ticker"].astype(str)
    frac = t.str.endswith("F")
    root = t.str.replace(r"F$", "", regex=True).replace(TICKER_RENAMES)
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
}


# B3 ticker shape: first char is a letter, then 3 alphanumeric, then 1-2 digits.
# Allows embedded digits in the prefix (e.g. B3SA3, M1TA34) which a strict
# [A-Z]{4} rule would wrongly reject.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}$")


def _extract_ticker(produto: str) -> str | None:
    """Extract the equity/FII ticker from a Movimentação 'Produto' cell.

    Format is typically 'TICKER - COMPANY NAME' for exchange-traded names,
    or 'CRI - <code> - <issuer>' / 'CDB - ...' / 'CFF - ...' for non-exchange
    products. We only care about the first form.
    """
    if not isinstance(produto, str):
        return None
    head = produto.split(" - ", 1)[0].strip().upper()
    return head if _TICKER_RE.fullmatch(head) else None


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

    trade_rows = raw[raw["category"] == "trade"].copy()
    # Direction -> Compra/Venda; skip rows missing essential numeric fields.
    trade_rows["side"] = trade_rows["direction"].map(
        lambda d: "Compra" if d.startswith("credito") else "Venda")
    trade_rows = trade_rows.dropna(subset=["date", "ticker", "qty", "price"])
    trade_rows = trade_rows[(trade_rows["qty"] > 0) & (trade_rows["price"] > 0)]
    trade_rows["market"] = "Mercado à Vista"  # Movimentação doesn't expose this
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
    keep_categories = ("cash", "reembolso", "amort", "qty_event", "other")
    cash_rows = raw[raw["category"].isin(keep_categories)].copy()
    cash_rows = cash_rows.dropna(subset=["date", "ticker"])
    # event_type stays human-readable so users can grep cash_events.csv later.
    cash_rows["event_type"] = cash_rows["mov_type"]
    # Map internal category -> stable public label written to CSV.
    _label = {"cash": "income", "reembolso": "reembolso",
              "amort": "amortization", "qty_event": "qty_event",
              "other": "other"}
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
    """Return 'negociacao' or 'movimentacao' by sniffing the first sheet's columns."""
    df = pd.read_excel(path, sheet_name=0, engine="openpyxl", nrows=0)
    cols = set(df.columns)
    if {"Tipo de Movimentação", "Código de Negociação"}.issubset(cols):
        return "negociacao"
    if {"Movimentação", "Produto", "Entrada/Saída"}.issubset(cols):
        return "movimentacao"
    raise SystemExit(f"Could not classify {path.name}: columns = {sorted(cols)}")


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
    out["_canonical"] = out["ticker"].str.replace(r"F$", "", regex=True)
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
        t["ticker"] = t["ticker"].astype(str).str.replace(r"F$", "", regex=True)

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
            ce["ticker"] = ce["ticker"].astype(str).str.replace(r"F$", "", regex=True)
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
    file_kinds: dict[str, str] = {}
    for f in files:
        kind = detect_file_type(f)
        file_kinds[f.name] = kind
        if kind == "negociacao":
            neg_dfs.append(read_negociacao(f))
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
        is_rf_etf = (
            trades["market"].fillna("").astype(str).str.contains(
                "Renda Fixa", case=False, na=False)
            & trades["ticker"].astype(str).str.match(
                _TICKER_RE.pattern, na=False)
        )
        trades_for_positions = trades[is_equity_market | is_rf_etf]
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

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(TRADES_CSV, index=False)
    cash_events.to_csv(CASH_EVENTS_CSV, index=False)

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
