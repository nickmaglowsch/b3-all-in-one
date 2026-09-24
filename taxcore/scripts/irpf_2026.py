"""Generate an IRPF 2026 (tax year 2025) ready-to-copy report.

Produces a markdown report with three sections matching the IRPF program:
  1. Bens e Direitos  — per-position cost basis on 31/12/2024 and 31/12/2025
  2. Rendimentos      — dividends, JCP, FII rendimentos paid in 2025
  3. Renda Variável   — month-by-month operações comuns + FII summary for 2025

Methodology:
  - Walks the full trades + cash_events timeline twice (once stopping at
    2024-12-31, once at 2025-12-31) to snapshot qty + cost basis per ticker.
  - Classifies each ticker into an IRPF group/código (Bens e Direitos table).
  - Uses preço médio (PM) per Receita Federal IN 1.585/2015.
  - For Renda Variável: applies the R$ 20k monthly isenção to AÇÕES,
    20% rate to FIIs, tracks loss carryforward within each asset class.

Usage:
  python -m scripts.irpf_2026 --year 2025 > reports/$(date +%Y-%m-%d)-irpf-2026.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from scripts import load_b3 as _load_b3
# The apuração rules live in their own module — see scripts/apuracao.py for why.
# Re-exported here because callers and tests import them from this script.
from scripts.apuracao import CLASSES_APURACAO, monthly_apuracao

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRADES = DATA / "trades.csv"
EVENTS = DATA / "cash_events.csv"

# Tickers ending in "11" that are STOCK UNITS (ON+PN bundles), not FIIs.
# Units are classified for IRPF as ações (group 03 code 01), pay JCP/dividends,
# and are taxed under the equity rules (R$ 20k/month isenção). Add to this set
# whenever a new unit ticker appears in the user's history.
UNITS_NOT_FII: set[str] = {
    "SAPR11",  # Sanepar
    "TAEE11",  # Taesa
    "KLBN11",  # Klabin
    "BPAC11",  # BTG Pactual
    "ENGI11",  # Energisa
    "ALUP11",  # Alupar
    "SANB11",  # Santander Brasil
    "AESB11",  # AES Brasil
    "JPSA11",  # JSL/Simpar
    "ENMT11",  # Energisa MT
}


# Renda fixa contracts, identified by the prefix B3 puts in front of the
# contract code ('CDB - CDB24C7HQ2L'). Case-insensitive because B3 caps the bank
# paper and writes the Tesouro titles mixed-case ('Tesouro Selic 2029'), which a
# case-sensitive ^TESOURO would miss entirely.
#
# Imported from load_b3 rather than copied. Both are `scripts.*` in one venv, so
# the "different processes" argument that justifies the *platform's* copy does
# not apply here — and a second copy is what let the round-lot strip diverge
# between the two files once already.
RF_PREFIX_RE = _load_b3._RF_PREFIX_RE


def asset_class(t: str) -> str:
    """Map a ticker to its tax asset class (AÇÃO / FII / BDR / ETF_RV / ETF_RF / RF)."""
    t = t.upper()
    if t == "IVVB11":
        return "ETF_RV"
    if t == "LFTB11":
        return "ETF_RF"
    # Renda fixa contracts carry a contract code, not a ticker, so every shape
    # rule below misses and they would fall through to AÇÃO. That is not a
    # cosmetic mislabel: a CDB resgate booked as a stock sale inflates the
    # month's AÇÃO gross, can blow the R$20k isenção for genuine equity sales,
    # and taxes tributação-exclusiva income as renda variável. tlh_plan imports
    # this same function, so one branch here covers both callers.
    if RF_PREFIX_RE.match(t):
        return "RF"
    if t in UNITS_NOT_FII:
        return "AÇÃO"
    if t.endswith("11"):
        return "FII"
    if t.endswith(("31", "32", "34", "35")):
        return "BDR"
    return "AÇÃO"


# IRPF Bens e Direitos group/código mapping (per RFB Instrução Normativa).
# Tuple = (group_code, item_code, label_for_discriminação)
def irpf_classification(ticker: str) -> tuple[str, str, str]:
    t = ticker.upper()
    if t == "IVVB11":
        return ("07", "09", "Cotas de Fundo de Índice (ETF) - IVVB11 (iShares S&P 500)")
    if t == "LFTB11":
        return ("04", "02", "Cotas de ETF de Renda Fixa - LFTB11 (Tesouro Selic)")
    if t in UNITS_NOT_FII:
        return ("03", "01", "Ações (Unit)")
    if t.endswith("11"):
        return ("07", "03", "Cotas de Fundo de Investimento Imobiliário (FII)")
    if t.endswith("31") or t.endswith("32") or t.endswith("34") or t.endswith("35"):
        return ("03", "09", "BDR — Brazilian Depositary Receipt")
    # 3 / 4 / 5 / 6 → ações
    return ("03", "01", "Ações")


# Strip Brazilian corporate suffix variants (S/A, S.A, S.A., SA) so that
# "BANCO PINE S/A" and "BANCO PINE S.A." compare equal. The lookahead — rather
# than a trailing \b — handles the "S.A." case where two non-word characters
# meet (\b only fires on word/non-word transitions).
_SUFFIX_RE = re.compile(r"\bS[./]?A\.?(?![A-Z0-9])")


def _normalize_company(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = _SUFFIX_RE.sub("", name.upper())
    return re.sub(r"\s+", " ", s).strip(" .,/-")


# Broker and issuer CNPJ maps. Keys are stored already-normalized (suffix
# stripped, uppercased) so lookups don't pay the normalize cost per key —
# only the inbound name is normalized.

# Known broker CNPJs (stable, public registries).
BROKER_CNPJ = {
    "ITAU CV": "61.194.353/0001-64",
    "ORAMA DTVM": "13.293.225/0001-25",
    "ÓRAMA DTVM": "13.293.225/0001-25",
    "XP INVESTIMENTOS CCTVM": "02.332.886/0011-78",
    "RICO INVESTIMENTOS": "13.434.335/0001-60",
    "BTG PACTUAL CTVM": "43.815.158/0001-22",
    "CLEAR CORRETORA": "02.332.886/0011-78",
}


# Bank/issuer CNPJs for renda-fixa (CDB/LCI/LCA/CRI) emitters.
# Use the issuer's CNPJ (the institution that ISSUED the title), not the broker.
# Confidence: high for the well-known names; (verificar) for the smaller ones.
ISSUER_CNPJ = {
    "ITAU UNIBANCO": "60.701.190/0001-04",
    "BANCO PAN": "59.285.411/0001-13",
    "BANCO BMG": "61.186.680/0001-74",
    "BANCO ORIGINAL": "92.894.922/0001-08",
    "BANCO C6": "31.872.495/0001-72",
    "BANCO C6 CONSIGNADO": "61.348.538/0001-86",
    "BANCO PINE": "62.144.175/0001-20",
    "BANCO XP": "33.264.668/0001-03",
    "BANCO PAULISTA": "61.820.817/0001-09",
    "BANCO MASTER": "33.923.798/0001-00",
    "BANCO MASTER DE INVESTIMENTO": "33.923.798/0001-00",
    "BANCO FIBRA": "58.616.418/0001-08",
    "BANCO AGIBANK": "10.664.513/0001-50",
    "BANCO ABC-BRASIL": "28.195.667/0001-06",
    "BANCO BTG PACTUAL": "30.306.294/0001-45",
    "BRB": "00.000.208/0001-00",
    "BANCO VOITER": "61.024.352/0001-71",
    "BANCO STELLANTIS": "71.027.866/0001-34",
    "BANCO FIDIS": "71.027.866/0001-34",  # renamed to Stellantis
    "FACTA FINANCEIRA CFI": "15.581.638/0001-82",
    "BANCO DIGIMAIS": "92.874.270/0001-40",
    "OPEA SECURITIZADORA": "02.773.542/0001-22",
    "RB SEC COMPANHIA DE SECURITIZA": "09.625.207/0001-07",
    # Lower-confidence (verify against broker informe):
    "BANCO XCMG BRASIL": "33.466.988/0001-38",  # (verificar)
    "WILL FINANCEIRA CREDITO FINANC": "17.535.640/0001-26",  # (verificar)
    "BANCO PLENO": "24.633.851/0001-38",  # (verificar)
}


def _issuer_cnpj(name: str) -> str:
    key = _normalize_company(name)
    if not key:
        return ""
    for k, v in ISSUER_CNPJ.items():
        if k in key:
            return v
    return ""


def _broker_cnpj(name: str) -> str:
    key = _normalize_company(name)
    if not key:
        return ""
    for k, v in BROKER_CNPJ.items():
        if k in key or key in k:
            return v
    return ""


def build_timeline() -> pd.DataFrame:
    """Per-ticker unified timeline of trades + qty_events + amortizations."""
    trades = pd.read_csv(TRADES, parse_dates=["date"])
    events = pd.read_csv(EVENTS, parse_dates=["date"])

    equity_pat = r"Variável|à Vista|Vista|Fracionário|Renda Fixa"
    trades = trades[trades["market"].astype(str).str.contains(
        equity_pat, case=False, na=False, regex=True)].copy()
    # For Renda Fixa, only keep proper ETF ticker shape (LFTB11 etc.) — not
    # CDB / LCI / LCA contract codes. We test the ticker against load_b3's
    # standard B3 stock-ticker pattern.
    is_rf = trades["market"].astype(str).str.contains("Renda Fixa", case=False, na=False)
    valid_ticker = trades["ticker"].astype(str).apply(
        lambda s: bool(_load_b3._TICKER_RE.match(s)))
    trades = trades[~is_rf | valid_ticker].copy()

    # Guarded by the round-lot shape: a bare F-strip would truncate a renda fixa
    # contract code ending in F. The pattern is load_b3's, not a copy of it.
    trades["ticker_root"] = trades["ticker"].str.replace(
        _load_b3._FRACTIONAL_RE, _load_b3._ROUND_LOT, regex=True)
    trades["is_buy"] = trades["side"].str.lower().str.startswith("compra")

    # Renda fixa cash events (amortização and coupon rows on a CRI, custody
    # freezes on a CDB) now carry the contract code as their ticker. They are
    # declared by renda_fixa_year_end at cost, in Bens e Direitos — letting them
    # into this timeline would open a second, competing position for the same
    # paper and feed it to the renda-variável apuração below. Mirrors the trade
    # filter above; the two must drop the same set.
    events = events[~events["ticker"].astype(str).str.match(
        RF_PREFIX_RE.pattern, case=False, na=False)].copy()

    trade_rows = pd.DataFrame({
        "ticker": trades["ticker_root"], "date": trades["date"],
        "kind": ["buy" if b else "sell" for b in trades["is_buy"]],
        "qty": trades["qty"].astype(float),
        "amount": trades["qty"].astype(float) * trades["price"].astype(float),
        "broker": trades.get("broker", ""),
    })
    # Only qty_events and amortizations move qty or cost; kept in file order.
    kind = events.get("event_category", pd.Series(index=events.index, dtype=object)).map(
        {"qty_event": "qty_event", "amortization": "amort"})
    events, kind = events[kind.notna()], kind.dropna()
    is_qty = kind == "qty_event"
    event_rows = pd.DataFrame({
        "ticker": events["ticker"].map(str), "date": events["date"], "kind": kind,
        "qty": events["qty"].astype(float).fillna(0.0).where(is_qty, 0.0),
        "amount": events["amount"].astype(float).fillna(0.0).where(~is_qty, 0.0),
        "broker": events.get("broker", ""),
    })
    rows = [f for f in (trade_rows, event_rows) if not f.empty]
    return pd.concat(rows, ignore_index=True).sort_values(
        ["ticker", "date"]).reset_index(drop=True)


def snapshot_positions(tl: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Walk timeline up to cutoff and return (ticker, qty, total_cost, broker)."""
    out = []
    for ticker, group in tl.groupby("ticker"):
        qty = 0.0
        cost = 0.0
        brokers = []
        for _, r in group.iterrows():
            if r["date"] > cutoff:
                break
            if r["kind"] == "buy":
                cost += r["amount"]; qty += r["qty"]
                if isinstance(r["broker"], str) and r["broker"].strip() and r["broker"] != "override":
                    brokers.append(r["broker"])
            elif r["kind"] == "sell":
                if qty > 0:
                    avg = cost / qty
                    sq = min(r["qty"], qty)
                    cost -= sq * avg
                    qty -= sq
            elif r["kind"] == "qty_event":
                qty += r["qty"]
            elif r["kind"] == "amort":
                cost = max(0.0, cost - r["amount"])
        if qty > 1e-6:
            # Most-recent non-empty broker for this ticker on or before cutoff.
            broker = brokers[-1] if brokers else ""
            out.append({"ticker": ticker, "qty": round(qty, 6),
                        "total_cost": round(cost, 2),
                        "broker": broker})
    return pd.DataFrame(out)


# IRPF código (grupo 04) and kind label per renda fixa prefix: 02 for taxed
# (CDB, RDB, Tesouro), 03 for isentos (LCI/LCA/CRI/CRA, debêntures
# incentivadas). An unlisted prefix is taxed and labelled as itself.
RF_KINDS = {
    **{p: ("03", f"{p} (renda fixa isenta)") for p in ("LCI", "LCA", "CRI", "CRA")},
    **{p: ("02", f"{p} (renda fixa tributada)") for p in ("CDB", "RDB", "LC", "LF")},
    **{p: ("02", f"{p} (Tesouro Direto)") for p in ("TESOURO", "LFT", "LTN", "NTN")},
    "DEB": ("03", "Debênture incentivada"),
}


def renda_fixa_year_end(year: int) -> pd.DataFrame:
    """Build per-product saldo snapshot at 31/12/(year-1) and 31/12/year.

    Returns one row per (code, bank) pair with:
      aplic_total, exit_date, status, value_prev, value_cur, irpf_grupo, irpf_codigo,
      plus the year's realized rendimento (rendimento_cur) and coupon income
      (juros_cur) for the Rendimentos ficha.

    Balances are preço médio over the contract's own quantities, so a partial
    resgate takes its share of the cost and leaves the rest declared, and an
    aplicação made *after* a year-end is not counted into that year-end.
    """
    files = sorted((ROOT / "b3_imports").glob("movimentacao*.xlsx"))
    if not files:
        return pd.DataFrame()
    mov_dfs = [pd.read_excel(f) for f in files]
    mov = pd.concat(mov_dfs, ignore_index=True)
    mov["Data"] = pd.to_datetime(mov["Data"], errors="coerce", dayfirst=True)
    mov["Valor"] = pd.to_numeric(mov["Valor da Operação"], errors="coerce")
    mov["dir"] = mov["Entrada/Saída"].astype(str).str.strip().str.lower()
    mov["mov_type"] = mov["Movimentação"].astype(str).str.strip()
    mov["Qtd"] = pd.to_numeric(mov.get("Quantidade"), errors="coerce")

    rf = mov[mov["Produto"].astype(str).str.match(
        RF_PREFIX_RE.pattern, case=False, na=False)].copy()

    def code_of(p):
        parts = [x.strip() for x in p.split(" - ")]
        return f"{parts[0]} - {parts[1]}" if len(parts) >= 2 else p

    def bank_of(p):
        parts = [x.strip() for x in p.split(" - ")]
        return parts[2] if len(parts) >= 3 else ""

    rf["code"] = rf["Produto"].map(code_of)
    rf["bank"] = rf["Produto"].map(bank_of)
    rf["prefix"] = rf["code"].str.split(" - ").str[0]

    rows = []
    prev_eoy = pd.Timestamp(f"{year-1}-12-31")
    cur_eoy = pd.Timestamp(f"{year}-12-31")
    for code, g in rf.groupby("code"):
        g = g.sort_values("Data")
        def cat(r):
            m = r["mov_type"]
            d = r["dir"]
            has_value = pd.notna(r["Valor"]) and float(r["Valor"]) > 0
            # Tesouro Direto is booked as a plain Compra / Venda, which
            # load_b3.MOV_CATEGORY has always read as a trade — so leaving them
            # out here dropped every Tesouro position from the report while its
            # trades parsed fine everywhere else. The two must agree.
            if m in ("APLICAÇÃO", "APLICAÇÃO/DEPÓSITO", "Compra"): return "aplic"
            if m in ("VENCIMENTO", "VENCIMENTO/RESGATE SALDO EM CONTA",
                     "RESGATE ANTECIPADO", "Resgate", "Venda"):
                return "resgate"
            # Labels B3 uses for both legs of a round trip; Entrada/Saída is the
            # only thing that separates them.
            if m in ("COMPRA / VENDA", "COMPRA/VENDA DEFINITIVA/CESSAO",
                     "MDA COMPRA/VENDA DEFINITIVA MERCADO PRIMARIO",
                     "Transferência - Liquidação"):
                return "aplic" if d.startswith("credito") else "resgate"
            if m == "RETIRADA DE CUSTÓDIA":
                return "frozen" if not has_value else "resgate"
            # Coupon paid during the contract's life (CRI/CRA/debênture). Real
            # income, unlike AMORTIZAÇÃO, which returns principal.
            if m in ("PAGAMENTO DE JUROS", "JUROS"):
                return "juros"
            # Principal returned mid-life (CRI / CRA / debênture): not income,
            # and not an exit — it reduces the cost basis and leaves the
            # quantity alone, exactly as load_b3.derive_positions treats it.
            if m in ("AMORTIZAÇÃO", "Amortização"):
                return "amort"
            return "other"
        g = g.assign(cat=g.apply(cat, axis=1))

        aplic_total = g.loc[g["cat"] == "aplic", "Valor"].sum()
        aplic_dates = g.loc[g["cat"] == "aplic", "Data"]
        first_aplic = aplic_dates.min() if not aplic_dates.empty else None
        exit_rows = g[g["cat"].isin(["resgate", "frozen"])]
        first_exit = exit_rows["Data"].min() if not exit_rows.empty else None
        is_frozen = (g["cat"] == "frozen").any()

        # One chronological walk in preço médio, the same accounting
        # load_b3.derive_positions applies to every other holding — so the two
        # surfaces cannot report different cost bases for one contract.
        #
        # Three things it fixes at once: summing every aplicação regardless of
        # date reported a contract's *final* size as its 31/12/(Y-1) balance;
        # treating the first resgate as terminal wrote a partially redeemed
        # contract out of Bens e Direitos entirely; and an amortização (CRI /
        # CRA principal returned in instalments) never reduced the declared
        # cost at all. Quantities are what let a partial resgate take its
        # share; a report carrying none falls back to all-or-nothing.
        walk = g.sort_values("Data")
        by_qty = float(walk.loc[walk["cat"] == "aplic", "Qtd"].fillna(0).sum()) > 0

        def _num(v):
            return float(v) if pd.notna(v) else 0.0

        qty = cost = 0.0
        value_prev = value_cur = None
        rendimento_cur = juros_cur = 0.0
        # True when a resgate drew on cost this extract never saw — the aplicação
        # predates the downloaded window. Its gain cannot be computed from these
        # rows at all, so the report says so instead of declaring the proceeds as
        # if they were all profit. Mirrors load_b3's `incomplete_history`.
        incomplete = False
        for _, r in walk.iterrows():
            if value_prev is None and r["Data"] > prev_eoy:
                value_prev = cost
            if value_cur is None and r["Data"] > cur_eoy:
                value_cur = cost
            v, q, when = _num(r["Valor"]), _num(r["Qtd"]), r["Data"]
            if r["cat"] == "aplic":
                cost += v
                qty += q
            elif r["cat"] == "amort":
                # Clamped, exactly as derive_positions:819 clamps it. An extract
                # whose window opens mid-life shows instalments whose principal
                # was never seen going in, and an unclamped subtraction prints a
                # negative custo de aquisição straight into Bens e Direitos.
                if v > cost:
                    incomplete = True
                cost = max(0.0, cost - v)
            elif r["cat"] == "juros" and when.year == year:
                juros_cur += v
            elif r["cat"] == "resgate":
                # How much of the position left, as a fraction of the cost still
                # carried. Quantity decides when the row has one — that is preço
                # médio. When it does not, the money does: consume cost up to the
                # proceeds. That fallback is what the all-or-nothing branch was
                # supposed to be and never was (with no quantities anywhere `qty`
                # stayed 0, so `consumed` was always 0: a fully redeemed contract
                # stayed declared at full cost *and* booked its own principal as
                # income). Scaling `qty` by the same share keeps the two
                # consistent for whichever row comes next.
                if by_qty and q > 0 and qty > 0:
                    share = min(q, qty) / qty
                elif cost > 0:
                    share = min(v, cost) / cost
                else:
                    # Proceeds against no cost at all: everything this contract
                    # was bought with is outside the extract.
                    share = 0.0
                    if v > 0:
                        incomplete = True
                consumed = cost * share
                if when.year == year and not incomplete:
                    rendimento_cur += v - consumed
                cost -= consumed
                qty -= qty * share
            # "frozen" deliberately consumes nothing: a custody withdrawal that
            # never settled paid the holder nothing, so the paper is still owned
            # and still declarable — which is what the ⚠ note in the report says.
        value_prev = cost if value_prev is None else value_prev
        value_cur = cost if value_cur is None else value_cur

        held_prev = value_prev > 0.005
        held_cur = value_cur > 0.005
        # A contract opened and fully redeemed inside the year holds nothing at
        # either year-end, but its gain is still declarable — so it stays.
        redeemed_in_year = ((g["cat"] == "resgate")
                            & (g["Data"].dt.year == year)).any()
        # Coupons and the incomplete flag keep a contract in the report even when
        # it holds nothing at either year-end: a CRI whose aplicação predates the
        # downloaded window still paid declarable isento juros this year, and
        # dropping the row would lose that income silently rather than disclose
        # the gap — which is the wrong half of the trade-off to take.
        if not (held_prev or held_cur or redeemed_in_year
                or abs(juros_cur) > 0.005 or incomplete):
            continue  # not relevant for IRPF this year

        rendimento_cur = round(rendimento_cur, 2)

        bank = next((b for b in g["bank"] if isinstance(b, str) and b), "")
        # Upper-cased for the buckets below, which are all upper-case literals.
        # B3 caps the bank paper but writes Tesouro titles mixed-case, so
        # "Tesouro Selic 2029" missed every branch and landed on the `else` —
        # which happens to be the right código for Tesouro and would be the
        # *wrong* one for a mixed-case "Debênture" (tributado instead of isento).
        prefix = code.split(" - ")[0].upper()

        irpf_c, kind_label = RF_KINDS.get(prefix, ("02", prefix))

        rows.append({
            "code": code,
            "bank": bank,
            "kind_label": kind_label,
            "aplic_value": round(aplic_total, 2),
            "first_aplic": first_aplic,
            "exit_date": first_exit,
            "is_frozen": is_frozen,
            "value_prev": round(value_prev, 2),
            "value_cur": round(value_cur, 2),
            "rendimento_cur": rendimento_cur,
            "juros_cur": round(juros_cur, 2),
            "incomplete": incomplete,
            "irpf_grupo": "04",
            "irpf_codigo": irpf_c,
        })
    return pd.DataFrame(rows)


def realized_per_month(tl: pd.DataFrame, year: int) -> pd.DataFrame:
    """Per-month per-ticker realized P&L for the given calendar year."""
    rows = []
    for ticker, group in tl.groupby("ticker"):
        qty = 0.0; cost = 0.0
        for _, r in group.iterrows():
            if r["kind"] == "buy":
                cost += r["amount"]; qty += r["qty"]
            elif r["kind"] == "sell":
                if qty > 0:
                    avg = cost / qty
                    sq = min(r["qty"], qty)
                    sp = r["amount"] / r["qty"] if r["qty"] else 0.0
                    pnl = sq * (sp - avg)
                    cost -= sq * avg; qty -= sq
                    if r["date"].year == year:
                        rows.append({"date": r["date"], "ticker": ticker,
                                     "qty": sq, "sell_price": sp, "avg_cost": avg,
                                     "sell_value": sq * sp, "pnl": pnl})
            elif r["kind"] == "qty_event":
                qty += r["qty"]
            elif r["kind"] == "amort":
                cost = max(0.0, cost - r["amount"])
    return pd.DataFrame(rows)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, default=2025,
                   help="Calendar year covered (default 2025; IRPF 2026 covers tax year 2025)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Emit the Renda Variável apuração as JSON and exit, skipping "
                        "the markdown report (what the DARF ledger reads)")
    args = p.parse_args(argv[1:])
    Y = args.year

    tl = build_timeline()

    # ----- Renda Variável monthly grid -----
    # Built before anything else so --json can return without touching the raw
    # extracts: the apuração needs only trades + cash_events, while Bens e
    # Direitos and the renda fixa sections parse every uploaded Excel file.
    realized = realized_per_month(tl, Y)

    realized["cls"] = realized["ticker"].map(asset_class)
    realized["month"] = realized["date"].dt.month

    monthly = []
    for m in range(1, 13):
        sub = realized[realized["month"] == m]
        row = {"month": m}
        for cls in ("AÇÃO", "FII", "BDR", "ETF_RV", "ETF_RF"):
            x = sub[sub["cls"] == cls]
            row[f"{cls}_gross"] = round(x["sell_value"].sum(), 2)
            row[f"{cls}_pnl"] = round(x["pnl"].sum(), 2)
        monthly.append(row)
    monthly_df = pd.DataFrame(monthly)

    apuracao = monthly_apuracao(monthly_df)
    if args.as_json:
        json.dump({"year": Y, "apuracao": apuracao}, sys.stdout)
        return 0

    by_class = {cls: [r for r in apuracao if r["classe"] == cls]
                for cls in CLASSES_APURACAO}

    months_pt_for_table = ["Jan","Fev","Mar","Abr","Mai","Jun",
                           "Jul","Ago","Set","Out","Nov","Dez"]

    # AÇÕES isento gains (vendas mensais ≤ R$ 20k) land in Rendimentos Isentos
    # código 20, which is rendered before the monthly table — hence read off the
    # apuração here rather than recomputed there.
    acoes_isento_breakdown = [r for r in by_class["AÇÃO"] if r["ganho_isento"] > 0]
    acoes_isento_gains_total = round(
        sum(r["ganho_isento"] for r in acoes_isento_breakdown), 2)

    # ----- Bens e Direitos snapshots -----
    snap_prev = snapshot_positions(tl, pd.Timestamp(f"{Y-1}-12-31"))
    snap_cur = snapshot_positions(tl, pd.Timestamp(f"{Y}-12-31"))
    snap_prev = snap_prev.rename(columns={"qty": "qty_prev",
                                          "total_cost": "value_prev",
                                          "broker": "broker_prev"})
    snap_cur = snap_cur.rename(columns={"qty": "qty_cur",
                                        "total_cost": "value_cur",
                                        "broker": "broker_cur"})
    bens = snap_prev.merge(snap_cur, on="ticker", how="outer").fillna(
        {"qty_prev": 0, "value_prev": 0, "qty_cur": 0, "value_cur": 0,
         "broker_prev": "", "broker_cur": ""})
    bens = bens[(bens["qty_prev"] > 0) | (bens["qty_cur"] > 0)].copy()
    bens["broker"] = bens["broker_cur"].where(bens["broker_cur"].astype(str).str.strip() != "",
                                              bens["broker_prev"])
    bens[["group", "code", "label"]] = pd.DataFrame(
        bens["ticker"].map(irpf_classification).tolist(), index=bens.index)
    bens = bens.sort_values(["group", "code", "ticker"]).reset_index(drop=True)

    # ----- Income aggregations for the year -----
    events = pd.read_csv(EVENTS, parse_dates=["date"])
    yr = events[events["date"].dt.year == Y].copy()

    def by_type(et_keyword):
        return yr[yr["event_type"].astype(str).str.contains(et_keyword, case=False, na=False)]

    dividendos = by_type("Dividendo")
    dividendos = dividendos[dividendos["event_category"] == "income"]
    jcp = by_type("Juros Sobre")
    jcp = jcp[jcp["event_category"] == "income"]
    rendimentos = by_type("Rendimento")
    rendimentos = rendimentos[rendimentos["event_category"] == "income"]
    reembolsos = yr[yr["event_category"] == "reembolso"]
    premios = yr[yr["event_category"] == "premium"]

    # ----- Render markdown -----
    out: list[str] = []
    w = out.append

    w(f"# IRPF 2026 — relatório copy-paste (ano-base {Y})\n")
    w(f"**Date generated**: {date.today().isoformat()}")
    w(f"**Ano-base**: {Y} (DIRPF entregue em 2026)")
    w(f"**Fonte**: `data/trades.csv` + `data/cash_events.csv` após `python -m scripts.load_b3 --all-markets`")
    w(f"**Metodologia**: custo médio (PM) por ticker, isenção R$ 20.000/mês para AÇÕES em operações comuns.\n")

    w("> ⚠ **Não é orientação fiscal.** Confira tudo contra os Informes de Rendimentos dos brokers antes de submeter. CNPJs de pagadores (companhias / FIIs) você obtém do informe — este relatório fornece os totais e codifica os bens.\n")

    w(f"\n## 1) Bens e Direitos — Ficha Bens e Direitos\n")
    w(f"Para cada posição abaixo, abra **Bens e Direitos → Novo** no programa IRPF, "
      f"escolha o Grupo e Código indicados, cole a Discriminação, e preencha "
      f"`Situação em 31/12/{Y-1}` e `Situação em 31/12/{Y}` com os valores em R$.\n")
    w(f"**Localização** = `105 - Brasil` em todas as linhas (mesmo para IVVB11 e BDRs — o "
      f"ETF/BDR é constituído no Brasil, apenas a exposição é externa).\n")

    for (grp, cod, label), g in bens.groupby(["group", "code", "label"], sort=False):
        w(f"\n### Grupo {grp} · Código {cod} — {label}\n")
        w("| Ticker | Qtde 31/12/{0} | Valor 31/12/{0} (R$) | Qtde 31/12/{1} | Valor 31/12/{1} (R$) | Broker | Discriminação sugerida |".format(Y-1, Y))
        w("|---|---|---|---|---|---|---|")
        for _, r in g.iterrows():
            broker = r["broker"] if isinstance(r["broker"], str) else ""
            cnpj = _broker_cnpj(broker)
            cnpj_str = f", CNPJ {cnpj}" if cnpj else ""
            qty_cur_display = (int(r['qty_cur']) if r['qty_cur'] == int(r['qty_cur'])
                               else f"{r['qty_cur']:.4f}")
            qty_prev_display = (int(r['qty_prev']) if r['qty_prev'] == int(r['qty_prev'])
                                else f"{r['qty_prev']:.4f}")
            qty_for_disc = qty_cur_display if r['qty_cur'] > 0 else qty_prev_display
            avg_cost_cur = (r['value_cur'] / r['qty_cur']) if r['qty_cur'] > 0 else (
                r['value_prev'] / r['qty_prev'] if r['qty_prev'] > 0 else 0)
            disc = f"{qty_for_disc} cotas/ações de {r['ticker']}, custódia em {broker}{cnpj_str}, custo médio R$ {avg_cost_cur:.4f}/un"
            w(f"| **{r['ticker']}** | {qty_prev_display} | {r['value_prev']:,.2f} "
              f"| {qty_cur_display} | {r['value_cur']:,.2f} | {broker or '?'} | {disc} |")

    # Renda fixa (CDB/LCI/LCA/CRI) inventory with year-end snapshots.
    rf = renda_fixa_year_end(Y)
    if not rf.empty:
        # Group by IRPF code
        # Tributados (04-02): CDB, RDB, LC, LF, Tesouro
        # Isentos (04-03): LCI, LCA, CRI, CRA, debêntures incentivadas
        tributados = rf[rf["irpf_codigo"] == "02"].copy().sort_values(
            ["bank", "code"]).reset_index(drop=True)
        isentos = rf[rf["irpf_codigo"] == "03"].copy().sort_values(
            ["bank", "code"]).reset_index(drop=True)

        if not tributados.empty:
            w(f"\n### Grupo 04 · Código 02 — Títulos privados sujeitos a tributação (CDBs / RDBs / Tesouro)\n")
            w(f"Use o **CNPJ do EMISSOR** (banco que emitiu o CDB), não do broker. "
              f"Para o saldo em 31/12, o ideal é usar o valor do Informe de Rendimentos "
              f"do broker (inclui juros acumulados). Na ausência, use o valor de aplicação "
              f"(aproximação conservadora para fins de IRPF — a Receita aceita o valor "
              f"de aquisição se o título não foi resgatado).\n")
            w("| Código do título | Emissor | CNPJ emissor | Valor 31/12/{0} (R$) | Valor 31/12/{1} (R$) | Status / observações |".format(Y-1, Y))
            w("|---|---|---|---|---|---|")
            for _, r in tributados.iterrows():
                cnpj = _issuer_cnpj(r["bank"])
                if not cnpj and r["bank"]:
                    cnpj = "(verificar no informe)"
                elif not cnpj:
                    cnpj = "(verificar)"
                status_note = ""
                if r["is_frozen"] or "MASTER" in (r["bank"] or "").upper():
                    status_note = "⚠ Banco em liquidação extrajudicial — declarar; FGC paga até R$ 250k/CPF"
                code_short = r["code"].split(" - ")[1] if " - " in r["code"] else r["code"]
                prefix = r["code"].split(" - ")[0]
                w(f"| {prefix} {code_short} | {r['bank'] or '(emissor não informado)'} | "
                  f"{cnpj} | {r['value_prev']:,.2f} | {r['value_cur']:,.2f} | {status_note} |")

        if not isentos.empty:
            w(f"\n### Grupo 04 · Código 03 — Títulos privados isentos de IR (LCIs / LCAs / CRIs / CRAs)\n")
            w(f"Use o **CNPJ do EMISSOR** (banco emissor / securitizadora). "
              f"Rendimentos pagos durante {Y} são isentos de IR (declarar em "
              f"Rendimentos Isentos código 12 \"Rendimentos de cadernetas, LCI, LCA, CRI, CRA\").\n")
            w("| Código do título | Emissor | CNPJ emissor | Valor 31/12/{0} (R$) | Valor 31/12/{1} (R$) | Tipo |".format(Y-1, Y))
            w("|---|---|---|---|---|---|")
            for _, r in isentos.iterrows():
                cnpj = _issuer_cnpj(r["bank"])
                if not cnpj and r["bank"]:
                    cnpj = "(verificar no informe)"
                elif not cnpj:
                    cnpj = "(verificar)"
                code_short = r["code"].split(" - ")[1] if " - " in r["code"] else r["code"]
                prefix = r["code"].split(" - ")[0]
                w(f"| {prefix} {code_short} | {r['bank'] or '(emissor não informado)'} | "
                  f"{cnpj} | {r['value_prev']:,.2f} | {r['value_cur']:,.2f} | {r['kind_label']} |")

        total_prev = float(rf["value_prev"].sum())
        total_cur = float(rf["value_cur"].sum())
        w(f"\n**Totais renda fixa (excl. ETFs LFTB11 já listados acima):**")
        w(f"\n- Posição em 31/12/{Y-1}: **R$ {total_prev:,.2f}**")
        w(f"\n- Posição em 31/12/{Y}: **R$ {total_cur:,.2f}**")
        w(f"\n*Os valores acima usam o **valor de aplicação** (custo de aquisição), "
          f"reduzido pelas amortizações já recebidas — o mesmo custo que a aba "
          f"Portfólio mostra. É deliberadamente **não** o valor corrigido que a "
          f"curva de rentabilidade desenha: aquele é um modelo, e a Receita pede "
          f"o custo. "
          f"Para o IRPF, em alguns casos a Receita pede o saldo com juros acumulados — "
          f"se o Informe de Rendimentos do broker mostrar saldos diferentes, prefira o "
          f"informe. A diferença não é material para títulos sem cupom (zero-coupon), "
          f"que são a maioria dos CDBs e LCs.*")
        w(f"\n*Não há posições de Tesouro Direto direto neste extrato — sua exposição "
          f"a Tesouro Selic é via o ETF **LFTB11** (Grupo 07 · Código 02), já listado "
          f"acima na seção de ETFs.*")
    else:
        w(f"\n### Grupo 04 · Código 02/03 — Renda Fixa\n")
        w(f"Nenhuma posição de CDB / LCI / LCA / CRI identificada nos relatórios de "
          f"Movimentação carregados.\n")

    w(f"\n## 2) Rendimentos recebidos em {Y}\n")

    w(f"\n### 2.1) Rendimentos Isentos e Não Tributáveis (Ficha)\n")
    w(f"**Código 09 — Lucros e dividendos recebidos**\n")
    div_total = float(dividendos["amount"].sum())
    if not dividendos.empty:
        by_ticker = dividendos.groupby("ticker")["amount"].sum().sort_values(ascending=False)
        w("| Ticker | Total R$ em {} | Fonte (preencher CNPJ do informe) |".format(Y))
        w("|---|---|---|")
        for tk, amt in by_ticker.items():
            w(f"| {tk} | {amt:,.2f} | (consultar Informe de Rendimentos) |")
        w(f"| **Total código 09** | **{div_total:,.2f}** | |")
    else:
        w(f"Nenhum dividendo de ações registrado em {Y}.")

    w(f"\n**Código 20 — Ganhos líquidos em alienações de ações ≤ R$ 20.000/mês**\n")
    w(f"Soma dos resultados positivos em meses cujo total de vendas de ações ficou "
      f"dentro do limite mensal de isenção de R$ 20.000. Esses ganhos são **isentos** "
      f"de IR e **não consomem o prejuízo acumulado** — o saldo de compensação fica "
      f"intacto para meses tributáveis futuros. (Prejuízos em meses isentos, por outro "
      f"lado, continuam sendo adicionados ao prejuízo acumulado normalmente — ver "
      f"tabela mensal na seção 3.)\n")
    if acoes_isento_breakdown:
        w("| Mês | Vendas brutas | Ganho isento |")
        w("|---|---|---|")
        for r in acoes_isento_breakdown:
            w(f"| {months_pt_for_table[r['month']-1]} | R$ {r['gross']:,.2f} | R$ {r['pnl']:+,.2f} |")
        w(f"| **Total código 20** | | **R$ {acoes_isento_gains_total:,.2f}** |")
        w(f"\n*Lance este total no IRPF em **Rendimentos Isentos e Não Tributáveis → Novo → "
          f"Código 20 — Ganhos líquidos em operações no mercado à vista de ações negociadas "
          f"em bolsas de valores nas alienações realizadas até R$ 20.000,00 em cada mês**, "
          f"como Beneficiário o próprio titular.*")
    else:
        w(f"Nenhum ganho isento por venda mensal ≤ R$ 20k em {Y}.")

    w(f"\n**Código 26 — Outros (Reembolsos)**")
    reemb_total = float(reembolsos["amount"].sum()) if not reembolsos.empty else 0
    if reemb_total > 0:
        w(f"\nTotal reembolsos em {Y}: **R$ {reemb_total:,.2f}**. Não é renda — registro tipicamente "
          f"como restituição/reembolso. Confirme com seu contador se vai em código 26 ou em "
          f"\"não declarável\" dependendo da natureza (IR retido a maior, taxa do broker, etc.).\n")
    else:
        w(f"\nNenhum reembolso registrado em {Y}.\n")

    w(f"\n**Código 99 — Outros — Rendimentos de FII (cotistas pessoa física)**\n")
    rend_total = float(rendimentos["amount"].sum())
    if not rendimentos.empty:
        by_ticker = rendimentos.groupby("ticker")["amount"].sum().sort_values(ascending=False)
        w("| FII | Total R$ em {} | CNPJ do fundo (do informe) |".format(Y))
        w("|---|---|---|")
        for tk, amt in by_ticker.items():
            w(f"| {tk} | {amt:,.2f} | (consultar Informe) |")
        w(f"| **Total rendimentos FII** | **{rend_total:,.2f}** | |")
        w(f"\n*(IRPF código 99 \"Outros\" se o fundo atende aos requisitos do art. 3º Lei 11.033/2004: "
          f"cotas em bolsa, mín. 50 cotistas, não pertencer a sócios ≥10%. Quase todos os FIIs líquidos "
          f"se enquadram.)*\n")
    else:
        w(f"Nenhum rendimento de FII registrado em {Y}.\n")

    w(f"\n### 2.2) Rendimentos Sujeitos à Tributação Exclusiva/Definitiva (Ficha)\n")
    w(f"**Código 10 — Juros sobre capital próprio (JCP)**\n")
    jcp_total = float(jcp["amount"].sum())
    if not jcp.empty:
        # JCP comes net of 15% IR retido at source. Gross = net / 0.85.
        by_ticker = jcp.groupby("ticker")["amount"].sum().sort_values(ascending=False)
        w("Valores recebidos LÍQUIDOS (após IR 15% retido na fonte). "
          "Para preencher \"Rendimento Líquido\", use o valor abaixo. Para \"IR retido na fonte\", "
          "use ~17,647% do valor líquido (= 15% sobre o bruto = líquido × 0,15/0,85).\n")
        w("| Ticker | JCP líquido em {} | IR retido (estimado) | CNPJ do informe |".format(Y))
        w("|---|---|---|---|")
        for tk, amt in by_ticker.items():
            ir_retido = amt * 0.15 / 0.85
            w(f"| {tk} | R$ {amt:,.2f} | R$ {ir_retido:,.2f} | (consultar Informe) |")
        w(f"| **Total JCP líquido** | **R$ {jcp_total:,.2f}** | R$ {jcp_total * 0.15/0.85:,.2f} | |")
    else:
        w(f"Nenhum JCP registrado em {Y}.\n")

    # Renda fixa rendimentos, per contract, from the same frame that built Bens
    # e Direitos. What the extract can support differs sharply by código: the
    # isento paper is complete, the tributado paper is net of IR retido and can
    # never be more than that here — so each says exactly what it is.
    rf_income = rf if not rf.empty else pd.DataFrame(
        columns=["code", "bank", "kind_label", "irpf_codigo",
                 "rendimento_cur", "juros_cur", "incomplete"])

    def _rf_rows(codigo):
        if rf_income.empty:
            return rf_income
        sel = rf_income[rf_income["irpf_codigo"] == codigo].copy()
        sel["total"] = sel["rendimento_cur"] + sel["juros_cur"]
        return sel[sel["total"].abs() > 0.005].sort_values(["bank", "code"])

    def _rf_incomplete(codigo):
        """Contracts whose gain this extract cannot compute — the aplicação (or
        the principal an amortização returned) is older than the downloaded
        window, so proceeds − cost is not a gain, it is a missing number."""
        if rf_income.empty or "incomplete" not in rf_income.columns:
            return rf_income.iloc[0:0]
        sel = rf_income[(rf_income["irpf_codigo"] == codigo)
                        & rf_income["incomplete"].astype(bool)]
        return sel.sort_values(["bank", "code"])

    def _rf_gap_note(codigo):
        """Name them under the table rather than letting the total imply they
        were counted. Same posture as load_b3's incomplete_history warning."""
        gaps = _rf_incomplete(codigo)
        if gaps.empty:
            return False
        names = ", ".join(
            f"{r['code'].split(' - ')[0]} "
            f"{r['code'].split(' - ')[1] if ' - ' in r['code'] else r['code']}"
            for _, r in gaps.iterrows())
        w(f"\n⚠ **Fora da conta acima:** {names}. O resgate aparece no extrato mas "
          f"a aplicação (ou parte dela) é anterior ao período baixado, então não há "
          f"custo com que apurar o rendimento — baixe um extrato mais antigo ou use "
          f"o Informe de Rendimentos para esses títulos.\n")
        return True

    w(f"\n**Código 06 — Rendimentos de aplicações financeiras (CDB / Tesouro / ETF RF)**")
    w(f"\nGanhos em CDB, RDB, Tesouro Direto e ETFs de Renda Fixa (LFTB11) são tributados "
      f"pela tabela regressiva e o IR já foi retido na fonte.\n")
    trib = _rf_rows("02")
    if not trib.empty:
        w(f"\n⚠ **Os valores abaixo são LÍQUIDOS.** O extrato da B3 registra o valor "
          f"*creditado* no resgate, já descontado o IR retido na fonte — o rendimento "
          f"**bruto** e o **IR retido** não existem em nenhum dos dois relatórios. "
          f"Use estes números para conferir o Informe de Rendimentos, e o informe para "
          f"preencher a declaração.\n")
        w("| Título | Emissor | Resgate − aplicação em {} (líquido, R$) |".format(Y))
        w("|---|---|---|")
        for _, r in trib.iterrows():
            code_short = r["code"].split(" - ")[1] if " - " in r["code"] else r["code"]
            w(f"| {r['code'].split(' - ')[0]} {code_short} | "
              f"{r['bank'] or '(emissor não informado)'} | {r['total']:,.2f} |")
        w(f"| **Total líquido apurado** | | **{trib['total'].sum():,.2f}** |")
        _rf_gap_note("02")
    else:
        w(f"\nNenhum resgate de renda fixa tributada em {Y} neste extrato. Use os totais "
          f"do Informe de Rendimentos do broker.\n")
        _rf_gap_note("02")

    w(f"\n**Código 12 — Rendimentos isentos: cadernetas, LCI, LCA, CRI, CRA, debêntures incentivadas**")
    w(f"\nRendimentos pagos em {Y} sobre LCIs, LCAs, CRIs (e CRAs / debêntures incentivadas, "
      f"se houver) são **isentos** de IR para pessoa física.\n")
    isento_inc = _rf_rows("03")
    if not isento_inc.empty:
        # The completeness claim is conditional on there being nothing missing.
        # An unqualified "completa, não uma aproximação" over a table that
        # silently omits a contract is the opposite of the honest-gaps rule.
        complete = _rf_incomplete("03").empty
        w(f"\nNão há retenção na fonte nesses títulos, então o valor creditado **é** o "
          + (f"rendimento — a apuração abaixo é completa, não uma aproximação. "
             if complete else
             f"rendimento. A apuração abaixo cobre os títulos cuja aplicação está "
             f"no extrato; veja a ressalva depois da tabela. ")
          + f"Some com quaisquer LCIs/LCAs mantidas fora da B3 antes de declarar.\n")
        w("| Título | Emissor | Resgate − aplicação (R$) | Juros pagos em {} (R$) | Total (R$) |".format(Y))
        w("|---|---|---|---|---|")
        for _, r in isento_inc.iterrows():
            code_short = r["code"].split(" - ")[1] if " - " in r["code"] else r["code"]
            w(f"| {r['code'].split(' - ')[0]} {code_short} | "
              f"{r['bank'] or '(emissor não informado)'} | "
              f"{r['rendimento_cur']:,.2f} | {r['juros_cur']:,.2f} | {r['total']:,.2f} |")
        w(f"| **Total isento** | | | | **{isento_inc['total'].sum():,.2f}** |")
        _rf_gap_note("03")
    else:
        w(f"\nNenhum rendimento isento de renda fixa apurado em {Y} neste extrato.\n")
        _rf_gap_note("03")

    w(f"\n**Código 12 — Outros — Premiums de opções (se aplicável)**")
    prem_total = float(premios["amount"].sum()) if not premios.empty else 0
    if abs(prem_total) > 0:
        w(f"\nPrêmio líquido de opções recebido em {Y}: **R$ {prem_total:,.2f}**. "
          f"Para fins de IRPF, prêmios são tributados como renda variável "
          f"(ganho/perda líquida mensal pela tabela 15%/20%). Inclua nas operações "
          f"comuns de cada mês — *não* na ficha rendimentos.\n")
    else:
        w(f"\nNenhum prêmio de opções registrado em {Y} (todas as opções foram pré-{Y-1}).\n")

    w(f"\n## 3) Renda Variável — Operações Comuns + FII ({Y})\n")
    w(f"Ficha **Renda Variável → Operações Comuns/Day-Trade**. Preencha por mês:\n")
    w(f"- **AÇÕES (operações comuns)**: isenção R$ 20.000/mês em vendas brutas. Se "
      f"o gross do mês for ≤ R$ 20k → isento (não preencha ganho líquido). Se > R$ 20k → "
      f"15% sobre o resultado líquido positivo, compensando prejuízos acumulados.\n")
    w(f"- **FIIs**: 20% sobre ganho líquido, sem isenção. Prejuízos de FII só compensam "
      f"ganhos futuros de FII.\n")
    w(f"- **BDR / ETF Renda Variável**: 15% sobre ganho líquido, sem isenção, mesma "
      f"linha de \"operações comuns\".\n")

    months_pt = months_pt_for_table
    w(f"\n### Tabela mensal {Y} — AÇÕES (operações comuns)")
    w(f"\n> **Atenção — o melhor dos dois mundos:** em meses com vendas brutas ≤ R$ 20.000, "
      f"o **ganho é isento** (vai para Rendimentos Isentos código 20, ver seção 2.1) e "
      f"**não consome o prejuízo acumulado** — o saldo de compensação fica intacto para "
      f"meses tributáveis futuros. Já o **prejuízo** num mês isento **continua sendo "
      f"adicionado ao prejuízo acumulado** e pode ser compensado em meses tributáveis "
      f"futuros normalmente.\n")
    w(f"\n| Mês | Vendas brutas | Resultado líquido | Compensação | IR a pagar (15%) | Prejuízo acumulado |")
    w(f"|---|---|---|---|---|---|")
    acoes_year_tax = 0
    for r in by_class["AÇÃO"]:
        m = r["month"]
        gross = r["gross"]; pnl = r["pnl"]; carry = r["carry"]
        if not r["ativo"]:
            w(f"| {months_pt[m-1]} | — | — | — | — | R$ {carry:,.2f} |")
            continue
        if r["isento"]:
            # Gains are tax-free (código 20) and DON'T consume the loss carry;
            # losses still accumulate and are compensable in future taxable
            # months (best-of-both-worlds rule — applied in monthly_apuracao).
            if pnl > 0:
                note = "**ISENTO** (≤ R$ 20k → código 20)"
            elif pnl < 0:
                note = "**ISENTO** (prejuízo somado ao acumulado)"
            else:
                note = "**ISENTO** (≤ R$ 20k)"
            w(f"| {months_pt[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | — | {note} | R$ {carry:,.2f} |")
            continue
        if pnl > 0:
            acoes_year_tax += r["ir"]
            w(f"| {months_pt[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | R$ {r['compensacao']:,.2f} | R$ {r['ir']:,.2f} | R$ {carry:,.2f} |")
        else:
            w(f"| {months_pt[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | — | R$ 0,00 (prejuízo) | R$ {carry:,.2f} |")
    acoes_carry = by_class["AÇÃO"][-1]["carry"]
    w(f"\n**Total IR sobre AÇÕES em {Y}**: R$ {acoes_year_tax:,.2f}")
    w(f"\n**Saldo prejuízo AÇÕES a compensar p/ {Y+1}**: R$ {acoes_carry:,.2f}")
    w(f"\n**Ganhos isentos AÇÕES em {Y} (vendas mensais ≤ R$ 20k)**: R$ {acoes_isento_gains_total:,.2f} → declarar em Rendimentos Isentos código 20.")

    w(f"\n### Tabela mensal {Y} — FIIs")
    w(f"\n| Mês | Vendas brutas | Resultado líquido | Compensação | IR a pagar (20%) | Prejuízo acumulado |")
    w(f"|---|---|---|---|---|---|")
    fii_year_tax = 0
    for r in by_class["FII"]:
        m = r["month"]
        gross = r["gross"]; pnl = r["pnl"]; carry = r["carry"]
        if not r["ativo"]:
            w(f"| {months_pt[m-1]} | — | — | — | — | R$ {carry:,.2f} |")
            continue
        if pnl > 0:
            fii_year_tax += r["ir"]
            w(f"| {months_pt[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | R$ {r['compensacao']:,.2f} | R$ {r['ir']:,.2f} | R$ {carry:,.2f} |")
        else:
            w(f"| {months_pt[m-1]} | R$ {gross:,.2f} | R$ {pnl:+,.2f} | — | R$ 0,00 | R$ {carry:,.2f} |")
    fii_carry = by_class["FII"][-1]["carry"]
    w(f"\n**Total IR sobre FIIs em {Y}**: R$ {fii_year_tax:,.2f}")
    w(f"\n**Saldo prejuízo FIIs a compensar p/ {Y+1}**: R$ {fii_carry:,.2f}")

    has_bdr_etf = monthly_df[["BDR_gross","ETF_RV_gross"]].sum().sum() > 0
    if has_bdr_etf:
        w(f"\n### Tabela mensal {Y} — BDRs e ETFs de Renda Variável (15% sem isenção)")
        w(f"\n| Mês | Vendas brutas | Resultado líquido | IR (15%) |")
        w(f"|---|---|---|---|")
        for r in by_class["BDR_ETF"]:
            m = r["month"]
            if not r["ativo"]:
                w(f"| {months_pt[m-1]} | — | — | — |")
                continue
            w(f"| {months_pt[m-1]} | R$ {r['gross']:,.2f} | R$ {r['pnl']:+,.2f} | R$ {r['ir']:,.2f} |")

    # ETF_RF (LFTB11) — different treatment
    has_rf_etf = monthly_df["ETF_RF_gross"].sum() > 0
    if has_rf_etf:
        w(f"\n### Tabela mensal {Y} — ETFs de Renda Fixa (LFTB11)")
        w(f"\n| Mês | Vendas brutas | Resultado líquido |")
        w(f"|---|---|---|")
        for _, r in monthly_df.iterrows():
            m = int(r["month"])
            if r["ETF_RF_gross"] == 0 and r["ETF_RF_pnl"] == 0:
                w(f"| {months_pt[m-1]} | — | — |")
                continue
            w(f"| {months_pt[m-1]} | R$ {r['ETF_RF_gross']:,.2f} | R$ {r['ETF_RF_pnl']:+,.2f} |")
        w(f"\n*ETFs de Renda Fixa (LFTB11 etc.) são tributados pela tabela regressiva da "
          f"renda fixa (22.5% se ≤180d, 20% 181-360d, 17.5% 361-720d, 15% > 720d) — "
          f"NÃO entram em \"Renda Variável\". Use os dados do Informe do broker para "
          f"preencher o ficha \"Rendimentos sujeitos a tributação exclusiva\".*")

    w(f"\n## 4) Checklist antes de submeter\n")
    w(f"- [ ] Conferir cada linha de \"Bens e Direitos\" (ações / FIIs / ETFs) contra o Informe de Posição do broker em 31/12/{Y}.")
    w(f"- [ ] Conferir saldos de CDBs / LCIs / LCAs / CRIs contra o Informe de Rendimentos do broker (valores com juros acumulados podem diferir do valor de aplicação aqui listado).")
    w(f"- [ ] Conferir totais de dividendos contra os Informes de Rendimentos das companhias / brokers.")
    w(f"- [ ] Conferir totais de JCP (líquido) e IR retido.")
    w(f"- [ ] Conferir rendimentos de FII (CNPJ de cada FII deve estar no informe).")
    w(f"- [ ] Verificar CNPJs marcados como \"(verificar)\" no relatório — bancos menores podem ter CNPJ diferente do estimado.")
    w(f"- [ ] Adicionar saldos de conta-corrente, poupança, cartão internacional (não cobertos aqui).")
    w(f"- [ ] Confirmar carryforward de prejuízos: este relatório já aplica isso na grade mensal. Se o prejuízo nunca foi declarado em anos anteriores, o saldo inicial deveria ser zerado.")
    w(f"- [ ] Verificar IR retido na fonte sobre swing/day-trade (DARFs pagas e \"IRRF a compensar\" no informe).")
    w(f"- [ ] Banco Master (em liquidação extrajudicial): declarar os CDBs normalmente em Bens e Direitos — o crédito existe (FGC paga). Quando FGC pagar (~30-90 dias), o resgate será informado no ano que cair.")

    return "\n".join(out)


if __name__ == "__main__":
    content = main(sys.argv)
    if isinstance(content, str):
        print(content)
    # If main returned int (exit code), nothing to print.
