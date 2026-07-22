"""Renda fixa (CDB / LCI / LCA / CRI / etc) inventory + return vs CDI.

Parses the fixed-income rows in `b3_imports/movimentacao*.xlsx` that the
main `load_b3.py` filters out, builds a per-product cash-flow timeline,
and compares each product's realized return against CDI accumulated over
the same window. CDI is fetched from Banco Central (BCB) series 12.

Output: per-product table + aggregate vs CDI summary. For products still
open (no maturity/redemption yet), we mark them OPEN and exclude from the
benchmark comparison since their fair value at the report date is unknown.

Usage:
    python -m scripts.renda_fixa_analysis
    python -m scripts.renda_fixa_analysis --json    # machine-readable
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
IMPORTS_DIR = ROOT / "b3_imports"

RF_PREFIX_PATTERN = r"^(CDB|LCI|LCA|LC|LF|CRA|CRI|TESOURO|DEB|NTN|LTN|LFT|RDB)\b"


def load_renda_fixa() -> pd.DataFrame:
    """Read all Movimentação files and return only fixed-income rows."""
    files = sorted(IMPORTS_DIR.glob("movimentacao*.xlsx"))
    if not files:
        raise SystemExit("No movimentacao*.xlsx in b3_imports/")
    dfs = [pd.read_excel(f) for f in files]
    mov = pd.concat(dfs, ignore_index=True)
    mov["Data"] = pd.to_datetime(mov["Data"], errors="coerce", dayfirst=True)
    mov["Valor"] = pd.to_numeric(mov["Valor da Operação"], errors="coerce")
    mov["dir"] = mov["Entrada/Saída"].astype(str).str.strip().str.lower()
    mov["mov_type"] = mov["Movimentação"].astype(str).str.strip()

    rf = mov[mov["Produto"].astype(str).str.match(RF_PREFIX_PATTERN, na=False)].copy()
    rf["code"] = rf["Produto"].map(_norm_code)
    rf["bank"] = rf["Produto"].map(_bank_name)
    rf["cat"] = rf.apply(_categorize, axis=1)
    return rf


def _norm_code(produto: str) -> str:
    parts = [p.strip() for p in produto.split(" - ")]
    return f"{parts[0]} - {parts[1]}" if len(parts) >= 2 else produto


def _bank_name(produto: str) -> str | None:
    parts = [p.strip() for p in produto.split(" - ")]
    return parts[2] if len(parts) >= 3 else None


def _categorize(row: pd.Series) -> str:
    """Map (Movimentação, Entrada/Saída) → semantic category.

    B3's Entrada/Saída is from the *custody* perspective, not the user's
    cash perspective: Credito = security flowed INTO custody (user paid
    cash); Debito = security flowed OUT of custody (user received cash).

    Categories (in user-cash terms):
      aplic     : cash OUT — user purchased the security
      resgate   : cash IN — security matured / redeemed for cash
      amort     : cash IN — principal returned without closing position
      juros     : cash IN — coupon paid
      transfer  : NO cash — broker-to-broker custody move (also matched by
                  pairs of Credito + Debito on the same day on the same
                  product, balanced)
      frozen    : Debito with Valor=0 — security removed without cash
                  (e.g. Banco Master em liquidação; FGC payout pending)
      other     : ignored
    """
    m = row["mov_type"]
    d = row["dir"]
    val = row.get("Valor")
    has_value = pd.notna(val) and float(val) > 0
    if m in ("APLICAÇÃO", "APLICAÇÃO/DEPÓSITO"):
        return "aplic"
    if m in ("COMPRA / VENDA", "COMPRA/VENDA DEFINITIVA/CESSAO",
             "MDA COMPRA/VENDA DEFINITIVA MERCADO PRIMARIO"):
        # Legacy B3 name for APLICAÇÃO; direction is always Credito for
        # renda fixa user purchases. Resgate uses RESGATE ANTECIPADO.
        return "aplic"
    if m in ("VENCIMENTO", "VENCIMENTO/RESGATE SALDO EM CONTA",
             "RESGATE ANTECIPADO", "Resgate"):
        return "resgate"
    if m in ("AMORTIZAÇÃO", "Amortização"):
        return "amort"
    if m == "PAGAMENTO DE JUROS":
        return "juros"
    if m == "RETIRADA DE CUSTÓDIA":
        # Debito with Valor=0 = bank failure / forced custody removal
        return "frozen" if not has_value else "resgate"
    if "TRANSFER" in m.upper() or "CUSTODIA" in m.upper() or "CUSTÓDIA" in m.upper():
        return "transfer"
    return "other"


def fetch_cdi_daily(start: str, end: str) -> pd.DataFrame:
    """Fetch BCB series 12 (daily CDI rate in %) between start/end.

    Returns DataFrame with columns: date, rate_pct (annualized daily rate as %).
    """
    url = (
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
        f"?formato=json&dataInicial={start}&dataFinal={end}"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["data"], dayfirst=True)
    df["rate_pct"] = df["valor"].astype(float)  # daily rate in %
    return df[["date", "rate_pct"]].sort_values("date").reset_index(drop=True)


def cdi_growth_factor(cdi: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Compound the daily CDI rate from start to end.

    BCB series 12 publishes the daily rate as a %. Daily compounding:
      factor = prod(1 + rate_pct / 100)
    Over n business days. Non-business days contribute factor=1.
    Returns the cumulative growth factor (1.0 = no growth).
    """
    mask = (cdi["date"] >= start) & (cdi["date"] <= end)
    sub = cdi.loc[mask]
    if sub.empty:
        return 1.0
    factors = 1.0 + sub["rate_pct"] / 100.0
    return float(factors.prod())


def per_product_analysis(rf: pd.DataFrame, cdi: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Build per-product cash flow + return + CDI-equivalent comparison.

    Statuses:
      CLOSED       — has both aplic and resgate; full realized return computable
      OPEN         — has aplic but no resgate yet; still held / not matured
      TRANSFERRED  — same product has transfer pairs that net to zero;
                     custody moved between brokers, no cash flow event
      FROZEN       — RETIRADA DE CUSTÓDIA with Valor=0 (Banco Master etc.)
      INCOMPLETE   — has resgate but no aplic visible (purchase date is
                     before the loaded report range)
    """
    rows = []
    for code, g in rf.groupby("code"):
        g = g.sort_values("Data")
        aplic_total = g.loc[g["cat"] == "aplic", "Valor"].sum()
        resgate_total = g.loc[g["cat"] == "resgate", "Valor"].sum()
        amort_total = g.loc[g["cat"] == "amort", "Valor"].sum()
        juros_total = g.loc[g["cat"] == "juros", "Valor"].sum()
        cash_in = resgate_total + amort_total + juros_total
        has_aplic = aplic_total > 0
        has_resgate = (g["cat"] == "resgate").any()
        has_frozen = (g["cat"] == "frozen").any()

        # Net transfer flow: if balanced Credito + Debito with TRANSFERENCIA
        # SEM FINANCEIRO and no other meaningful events after, the position
        # has been transferred to another broker (custody-only, no return).
        transfer_credito = g[(g["cat"] == "transfer") & (g["dir"] == "credito")]["Valor"].sum()
        transfer_debito = g[(g["cat"] == "transfer") & (g["dir"] == "debito")]["Valor"].sum()
        net_transfer_out = transfer_debito - transfer_credito  # positive → net OUT

        # Status determination
        # If the product has resgate rows but their total Valor is 0, the
        # security matured in custody but the cash flow happened outside B3's
        # report (issuer credited the user's brokerage account directly,
        # common for LCI/LCA/CDB from non-Itaú banks). We mark these
        # MATURED_NO_VALUE so they're excluded from realized-return math
        # rather than appearing as a fake 100% loss.
        resgate_with_value = g[(g["cat"] == "resgate") & (g["Valor"] > 0)]
        has_resgate_with_value = not resgate_with_value.empty
        has_resgate_no_value = has_resgate and not has_resgate_with_value and cash_in == 0
        if has_frozen:
            status = "FROZEN"
        elif has_resgate_no_value and has_aplic:
            status = "MATURED_NO_VALUE"
        elif has_resgate_with_value and has_aplic:
            status = "CLOSED"
        elif has_resgate and not has_aplic:
            status = "INCOMPLETE"
        elif has_aplic and net_transfer_out > 0 and not has_resgate:
            status = "TRANSFERRED"
        elif has_aplic:
            status = "OPEN"
        else:
            status = "OTHER"

        # Realized return only for CLOSED
        first_aplic = g.loc[g["cat"] == "aplic", "Data"].min() if has_aplic else None
        last_in = g.loc[g["cat"].isin(["resgate", "amort", "juros"]), "Data"].max() \
                  if has_resgate or amort_total > 0 or juros_total > 0 else None
        realized_return_pct = None
        days_held = None
        annualized_pct = None
        cdi_equivalent = None
        alpha_pp = None
        pct_of_cdi = None
        if status == "CLOSED" and aplic_total > 0:
            realized_return_pct = (cash_in / aplic_total - 1.0) * 100.0
            if first_aplic is not None and last_in is not None:
                days_held = (last_in - first_aplic).days
                if days_held > 0:
                    annualized_pct = (
                        (cash_in / aplic_total) ** (365.0 / days_held) - 1.0
                    ) * 100.0
                    cdi_factor = cdi_growth_factor(cdi, first_aplic, last_in)
                    cdi_equivalent = (cdi_factor - 1.0) * 100.0
                    if cdi_equivalent > 0:
                        alpha_pp = realized_return_pct - cdi_equivalent
                        pct_of_cdi = realized_return_pct / cdi_equivalent * 100

        rows.append({
            "code": code,
            "bank": next((b for b in g["bank"] if isinstance(b, str)), None),
            "status": status,
            "first_aplic": first_aplic,
            "last_event": g["Data"].max(),
            "days_held": days_held,
            "aplic_total": round(aplic_total, 2),
            "cash_in_total": round(cash_in, 2),
            "realized_pnl": round(cash_in - aplic_total, 2) if has_aplic else None,
            "realized_return_pct": round(realized_return_pct, 2) if realized_return_pct is not None else None,
            "annualized_pct": round(annualized_pct, 2) if annualized_pct is not None else None,
            "cdi_equivalent_pct": round(cdi_equivalent, 2) if cdi_equivalent is not None else None,
            "alpha_vs_cdi_pp": round(alpha_pp, 2) if alpha_pp is not None else None,
            "pct_of_cdi": round(pct_of_cdi, 1) if pct_of_cdi is not None else None,
        })
    out = pd.DataFrame(rows).sort_values("aplic_total", ascending=False).reset_index(drop=True)
    return out


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv[1:])

    rf = load_renda_fixa()
    if rf.empty:
        print("No renda-fixa rows found in b3_imports/.")
        return 0

    earliest = rf["Data"].min()
    latest = rf["Data"].max()
    today = pd.Timestamp.today().normalize()

    print(f"Fetching CDI series from BCB ({earliest.date()} → {today.date()})...",
          file=sys.stderr)
    cdi = fetch_cdi_daily(earliest.strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y"))

    df = per_product_analysis(rf, cdi, as_of=today)

    if args.json:
        print(df.to_json(orient="records", date_format="iso", indent=2))
        return 0

    def fmt_bank(b):
        return (b if isinstance(b, str) else "(unknown)")[:30]

    closed = df[df["status"] == "CLOSED"]
    open_ = df[df["status"] == "OPEN"]
    transferred = df[df["status"] == "TRANSFERRED"]
    frozen = df[df["status"] == "FROZEN"]
    incomplete = df[df["status"] == "INCOMPLETE"]
    matured_nv = df[df["status"] == "MATURED_NO_VALUE"]

    print()
    print("=" * 132)
    print(f"=== CLOSED PRODUCTS ({len(closed)}) — realized return vs CDI ===")
    print("=" * 132)
    for _, r in closed.iterrows():
        ret = f"{r['realized_return_pct']:>+6.2f}%" if r['realized_return_pct'] is not None else "    n/a"
        ann = f"{r['annualized_pct']:>+7.2f}%/yr" if r['annualized_pct'] is not None else "    n/a   "
        cdi = f"{r['cdi_equivalent_pct']:>+6.2f}%" if r['cdi_equivalent_pct'] is not None else "    n/a"
        pct = f"{r['pct_of_cdi']:>6.1f}%" if r['pct_of_cdi'] is not None else "   n/a"
        alpha = f"{r['alpha_vs_cdi_pp']:>+6.2f}pp" if r['alpha_vs_cdi_pp'] is not None else "    n/a "
        days = f"{int(r['days_held']):>4}d" if r['days_held'] else "  n/a"
        print(f"  {r['code']:<22} {fmt_bank(r['bank']):<32} R$ {r['aplic_total']:>9,.0f}→{r['cash_in_total']:>9,.0f}  "
              f"PnL {r['realized_pnl']:>+9,.2f}  {days}  ret {ret}  ann {ann}  cdi {cdi}  ({pct}) α {alpha}")

    if not open_.empty:
        print()
        print("=" * 132)
        print(f"=== OPEN PRODUCTS ({len(open_)}) — still held, awaiting maturity ===")
        print("=" * 132)
        for _, r in open_.sort_values("aplic_total", ascending=False).iterrows():
            print(f"  {r['code']:<22} {fmt_bank(r['bank']):<32} aplic R$ {r['aplic_total']:>10,.2f}  "
                  f"first {r['first_aplic'].date() if r['first_aplic'] else '?'}  "
                  f"last evt {r['last_event'].date() if r['last_event'] else '?'}")

    if not transferred.empty:
        print()
        print("=" * 132)
        print(f"=== TRANSFERRED TO ANOTHER BROKER ({len(transferred)}) — custody moved, no cash event ===")
        print("=" * 132)
        for _, r in transferred.sort_values("aplic_total", ascending=False).iterrows():
            print(f"  {r['code']:<22} {fmt_bank(r['bank']):<32} aplic R$ {r['aplic_total']:>10,.2f}  "
                  f"transferred out  last evt {r['last_event'].date() if r['last_event'] else '?'}")

    if not frozen.empty:
        print()
        print("=" * 132)
        print(f"⚠ FROZEN ({len(frozen)}) — security removed from custody without cash (bank intervention)")
        print("=" * 132)
        for _, r in frozen.sort_values("aplic_total", ascending=False).iterrows():
            print(f"  {r['code']:<22} {fmt_bank(r['bank']):<32} aplic R$ {r['aplic_total']:>10,.2f}  "
                  f"last evt {r['last_event'].date() if r['last_event'] else '?'}  → FGC payout pending")

    if not matured_nv.empty:
        print()
        print("=" * 132)
        print(f"=== MATURED, VALUE NOT IN B3 ({len(matured_nv)}) — security removed at maturity but cash credited via issuer/brokerage, not custody ===")
        print("=" * 132)
        for _, r in matured_nv.sort_values("aplic_total", ascending=False).iterrows():
            days = (r['last_event'] - r['first_aplic']).days if r['first_aplic'] is not None else None
            day_str = f"{days}d" if days else "?"
            print(f"  {r['code']:<22} {fmt_bank(r['bank']):<32} aplic R$ {r['aplic_total']:>10,.2f}  "
                  f"matured {r['last_event'].date() if r['last_event'] else '?'}  ({day_str} held)  "
                  f"→ check bank statement for actual payout")

    if not incomplete.empty:
        print()
        print("=" * 132)
        print(f"=== INCOMPLETE HISTORY ({len(incomplete)}) — resgate visible but aplicação predates report range ===")
        print("=" * 132)
        for _, r in incomplete.sort_values("cash_in_total", ascending=False).iterrows():
            print(f"  {r['code']:<22} {fmt_bank(r['bank']):<32} cash_in R$ {r['cash_in_total']:>10,.2f}  "
                  f"last evt {r['last_event'].date() if r['last_event'] else '?'}")

    print()
    print("=" * 132)
    print("=== AGGREGATES ===")
    print("=" * 132)
    total_invested_closed = closed["aplic_total"].sum() if not closed.empty else 0.0
    total_received_closed = closed["cash_in_total"].sum() if not closed.empty else 0.0
    realized_pnl_closed = total_received_closed - total_invested_closed
    print(f"CLOSED products ({len(closed)}):")
    print(f"  Total invested:     R$ {total_invested_closed:>12,.2f}")
    print(f"  Total received:     R$ {total_received_closed:>12,.2f}")
    print(f"  Realized P&L:       R$ {realized_pnl_closed:>+12,.2f}")
    if total_invested_closed > 0:
        agg_ret = (total_received_closed / total_invested_closed - 1.0) * 100.0
        print(f"  Aggregate return:   {agg_ret:>+12.2f}%")
        closed_with_cdi = closed.dropna(subset=["cdi_equivalent_pct"])
        if not closed_with_cdi.empty:
            w = closed_with_cdi["aplic_total"]
            w_cdi = (closed_with_cdi["cdi_equivalent_pct"] * w).sum() / w.sum()
            w_ret = (closed_with_cdi["realized_return_pct"] * w).sum() / w.sum()
            w_ann = (closed_with_cdi["annualized_pct"] * w).sum() / w.sum()
            print(f"  Weighted-avg holding:   realized {w_ret:>+6.2f}%   vs CDI {w_cdi:>+6.2f}%  "
                  f"=  {(w_ret / w_cdi * 100) if w_cdi > 0 else 0:>5.1f}% of CDI  "
                  f"(α {w_ret - w_cdi:>+5.2f}pp)")
            print(f"  Weighted-avg annualized: {w_ann:>+6.2f}%/yr")
    print()
    print(f"OPEN products ({len(open_)}):")
    print(f"  Total notional still held: R$ {open_['aplic_total'].sum() if not open_.empty else 0:>12,.2f}")
    print(f"TRANSFERRED ({len(transferred)}):")
    print(f"  Total notional moved out:  R$ {transferred['aplic_total'].sum() if not transferred.empty else 0:>12,.2f}")
    print(f"FROZEN (FGC pending) ({len(frozen)}):")
    print(f"  Total notional frozen:     R$ {frozen['aplic_total'].sum() if not frozen.empty else 0:>12,.2f}")
    print(f"MATURED_NO_VALUE ({len(matured_nv)}):")
    print(f"  Total aplicações on matured-but-untracked: R$ {matured_nv['aplic_total'].sum() if not matured_nv.empty else 0:>12,.2f}")
    print(f"  (cash flow happened outside B3 custody; check broker statement for exact payout)")
    print(f"INCOMPLETE ({len(incomplete)}):")
    print(f"  Total cash received from products without visible aplicação: R$ {incomplete['cash_in_total'].sum() if not incomplete.empty else 0:>12,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
