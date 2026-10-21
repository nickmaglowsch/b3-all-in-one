"""Tax-loss-harvesting (TLH) planner.

Interactive helper for offsetting realized profits with realized losses
within the same calendar month, exploiting the fact that Brazil has no
wash-sale rule — you can sell at a loss and rebuy the same ticker the
following trading day, and the loss IS recognized for IR.

Flow:
  1. Walk the full trade timeline from inception → current real prejuízo
     acumulado per bucket (AÇÕES, FIIs) and current-month realized state.
  2. Prompt the user for the profit-taking sell(s) they want to execute.
  3. Decide whether TLH is even needed (isenção R$20k or carry coverage).
  4. If needed, greedy-harvest the largest unrealized losers (by BRL) to
     bring the bucket's net monthly PnL to zero.
  5. Emit an execution plan with explicit T+1 rebuy lines and the
     day-trade / slippage / cost-basis caveats.

Usage:
  python -m scripts.tlh_plan
  python -m scripts.tlh_plan --exclude PETR4,VALE3
  python -m scripts.tlh_plan --no-save        # skip writing the report file

Non-interactive (for the platform to call):
  python -m scripts.tlh_plan --sells "PETR4:459,VALE3:100" --json
  python -m scripts.tlh_plan --sell PETR4:459 --sell VALE3:100   # repeatable
  # --json emits the plan as JSON to stdout (instead of the markdown) and
  # never writes a report file. With no --sells, --json just reports the
  # current situation (carry/mtd per bucket) with no plan.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from scripts._common import price_lookup as latest_prices
from scripts.apuracao import ALIQUOTA, ISENCAO_LIMIT  # one copy of the rules
from scripts.irpf_2026 import asset_class, build_timeline

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POSITIONS = DATA / "positions.csv"
REPORTS = ROOT / "reports"


def walk_history(tl: pd.DataFrame) -> tuple[dict, dict]:
    """Walk inception → today.

    Returns:
      carry: {"AÇÃO": x, "FII": y} — running prejuízo at the *start* of
             the current calendar month.
      mtd:   {"AÇÃO": {"gross": ..., "pnl": ...}, "FII": ...} for trades
             already executed in the current month.

    Current per-ticker PM is read separately from positions.csv (canonical;
    `build_timeline` filters by market and misses some in-kind transfers
    and corporate actions that load_b3 handles).
    """
    today = pd.Timestamp.today().normalize()
    month_start = today.replace(day=1)

    pm: dict[str, list[float]] = {}  # ticker -> [qty, cost]
    acoes_carry = 0.0
    fii_carry = 0.0
    mtd = {"AÇÃO": {"gross": 0.0, "pnl": 0.0},
           "FII":  {"gross": 0.0, "pnl": 0.0}}
    # Per-month aggregator: only used to apply isenção/carry rules
    # correctly month by month before reaching the current month.
    monthly: dict[pd.Period, dict] = {}

    def bump_monthly(period, cls, gross, pnl):
        m = monthly.setdefault(period, {
            "AÇÃO_gross": 0.0, "AÇÃO_pnl": 0.0,
            "FII_gross": 0.0, "FII_pnl": 0.0})
        if cls in ("AÇÃO", "FII"):
            m[f"{cls}_gross"] += gross
            m[f"{cls}_pnl"] += pnl

    for _, r in tl.iterrows():
        t = r["ticker"]
        state = pm.setdefault(t, [0.0, 0.0])
        qty, cost = state
        if r["kind"] == "buy":
            qty += r["qty"]; cost += r["amount"]
        elif r["kind"] == "sell" and qty > 0:
            avg = cost / qty
            sq = min(r["qty"], qty)
            sp = r["amount"] / r["qty"] if r["qty"] else 0
            pnl = sq * (sp - avg)
            cls = asset_class(t)
            period = r["date"].to_period("M")
            bump_monthly(period, cls, sq * sp, pnl)
            cost -= sq * avg; qty -= sq
        elif r["kind"] == "qty_event":
            qty += r["qty"]; cost += r["amount"]
        elif r["kind"] == "amort":
            cost = max(0.0, cost - r["amount"])
        state[0], state[1] = qty, cost

    # Replay monthly aggregates in chronological order, applying isenção
    # and carry rules per bucket. Stop *before* the current month so we
    # can report mtd separately.
    cur_period = month_start.to_period("M")
    for period in sorted(monthly.keys()):
        m = monthly[period]
        if period == cur_period:
            mtd["AÇÃO"]["gross"] = m["AÇÃO_gross"]
            mtd["AÇÃO"]["pnl"] = m["AÇÃO_pnl"]
            mtd["FII"]["gross"] = m["FII_gross"]
            mtd["FII"]["pnl"] = m["FII_pnl"]
            continue
        if period > cur_period:
            continue  # future-dated row (data anomaly); ignore
        ag, ap = m["AÇÃO_gross"], m["AÇÃO_pnl"]
        if ag > 0:
            if ag <= ISENCAO_LIMIT:
                # isento month: profits tax-free (não consome carry);
                # losses still accumulate into carry.
                if ap < 0:
                    acoes_carry += -ap
            else:
                if ap > 0:
                    offset = min(acoes_carry, ap)
                    acoes_carry -= offset
                else:
                    acoes_carry += -ap
        fg, fp = m["FII_gross"], m["FII_pnl"]
        if fg > 0:
            if fp > 0:
                offset = min(fii_carry, fp)
                fii_carry -= offset
            else:
                fii_carry += -fp

    return ({"AÇÃO": acoes_carry, "FII": fii_carry}, mtd)


def load_positions() -> dict[str, tuple[float, float]]:
    """{ticker: (qty, total_cost)} from positions.csv — canonical."""
    pos = pd.read_csv(POSITIONS)
    return {r["ticker"]: (float(r["qty"]), float(r["total_cost"]))
            for _, r in pos.iterrows() if float(r["qty"]) > 1e-6}


def prompt_sells(pm_now: dict) -> list[tuple[str, float]]:
    """Interactive prompt. Returns list of (ticker, qty) — validated against
    current holdings."""
    sells: list[tuple[str, float]] = []
    print()
    print("Informe as vendas que quer realizar (com lucro). Linha em branco para terminar.")
    print("Formato: TICKER QTDE   (ex: VALE3 200)")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            break
        parts = line.replace(",", " ").split()
        if len(parts) != 2:
            print("  ⚠ Use formato 'TICKER QTDE'. Tente de novo.")
            continue
        ticker = parts[0].upper()
        try:
            qty = float(parts[1])
        except ValueError:
            print(f"  ⚠ Quantidade inválida: {parts[1]}")
            continue
        if ticker not in pm_now:
            print(f"  ⚠ {ticker} não está em positions.csv.")
            continue
        held, _ = pm_now[ticker]
        if qty > held + 1e-6:
            print(f"  ⚠ {ticker}: você só tem {held:g} cotas, não dá pra vender {qty:g}.")
            continue
        sells.append((ticker, qty))
        print(f"  ✓ {ticker} {qty:g} adicionado.")
    return sells


def parse_sells(spec: list[str], pm_now: dict) -> list[tuple[str, float]]:
    """Non-interactive sells from ``TICKER:QTY`` tokens (CSV or repeated).

    Same validation as prompt_sells (ticker must be held, qty <= held) but a
    trust boundary for platform input, so any bad token raises instead of
    warn-and-skip.
    """
    sells: list[tuple[str, float]] = []
    for chunk in spec:
        for item in chunk.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError(f"formato inválido '{item}' — use TICKER:QTDE")
            tk, _, q = item.partition(":")
            ticker = tk.strip().upper()
            try:
                qty = float(q.strip())
            except ValueError:
                raise ValueError(f"quantidade inválida em '{item}'")
            if ticker not in pm_now:
                raise ValueError(f"{ticker} não está em positions.csv")
            if qty <= 0:
                raise ValueError(f"{ticker}: quantidade deve ser > 0")
            held, _ = pm_now[ticker]
            if qty > held + 1e-6:
                raise ValueError(
                    f"{ticker}: só há {held:g} cotas, não dá para vender {qty:g}")
            sells.append((ticker, qty))
    return sells


def build_result(sells_realized: list[dict], carry: dict, mtd: dict,
                 plan_acoes: list[dict], plan_fii: list[dict],
                 decision: dict) -> dict:
    """Machine-readable plan (the --json payload). Mirrors render_report's
    numbers: per-bucket status/carry/isenção, harvest sells, T+1 rebuys."""
    plans = {"AÇÃO": plan_acoes, "FII": plan_fii}
    buckets = {}
    for cls, d in decision.items():
        plan = plans.get(cls, [])
        buckets[cls] = {
            "status": d["status"],
            # Isento, month already at a loss and carry-covers-it all collapse
            # into the one status "Nenhum TLH necessário" — without the rationale
            # no caller but the markdown report can tell them apart.
            "rationale": d["rationale"],
            "gross_after": d["gross_after"],
            "pnl_after": d["pnl_after"],
            "carry_used": d["carry_used"],
            # Prejuízo left after the plan. Selling at a loss consumes no carry
            # and *adds* to it, which carry_used alone never showed. Derived, so
            # it is one formula rather than a sixth field each branch can forget.
            "carry_after": carry[cls] - d["carry_used"] + max(0.0, -d["final_pnl"]),
            "target_loss": d["target_loss"],
            "final_gross": d["final_gross"],
            "final_pnl": d["final_pnl"],
            "ir": d["ir"],
            "harvest": [{"ticker": p["ticker"], "qty": p["qty"],
                         "price": p["price"], "realized_loss": p["realized_loss"],
                         "partial": p["partial"]} for p in plan],
            "rebuys": [{"ticker": p["ticker"], "qty": p["qty"]} for p in plan],
        }
    return {
        "month": date.today().strftime("%m/%Y"),
        "generated": date.today().isoformat(),
        "isencao_limit": ISENCAO_LIMIT,
        "rates": {cls: ALIQUOTA[cls] for cls in ("AÇÃO", "FII")},
        "situation": {cls: {"gross_mtd": mtd[cls]["gross"],
                            "pnl_mtd": mtd[cls]["pnl"], "carry": carry[cls]}
                      for cls in ("AÇÃO", "FII")},
        "sells": [{"ticker": s["ticker"], "qty": s["qty"],
                   "avg_cost": s["avg_cost"], "price": s["price"],
                   "sell_value": s["sell_value"], "pnl": s["pnl"],
                   "cls": s["cls"]} for s in sells_realized],
        "buckets": buckets,
    }


def realize(sells: list[tuple[str, float]], pm_now: dict,
            prices: pd.DataFrame) -> list[dict]:
    """Compute realized PnL for each proposed sell using current PM and
    latest cotação as sell price. Returns enriched dicts."""
    price_map = dict(zip(prices["ticker"], prices["price"]))
    out = []
    for ticker, qty in sells:
        held_qty, total_cost = pm_now[ticker]
        avg = total_cost / held_qty if held_qty else 0.0
        px = price_map.get(ticker)
        if px is None or pd.isna(px):
            print(f"⚠ Sem cotação para {ticker} — pulando. "
                  f"Rode fetch_fundamentus.py ou fetch_external_prices.py.")
            continue
        pnl = qty * (px - avg)
        out.append({"ticker": ticker, "qty": qty, "avg_cost": avg,
                    "price": px, "sell_value": qty * px, "pnl": pnl,
                    "cls": asset_class(ticker)})
    return out


def find_losers(pm_now: dict, prices: pd.DataFrame, cls: str,
                exclude: set[str]) -> pd.DataFrame:
    """Open positions with negative unrealized PnL in the requested bucket,
    sorted by largest BRL loss first."""
    price_map = dict(zip(prices["ticker"], prices["price"]))
    rows = []
    for ticker, (qty, cost) in pm_now.items():
        if ticker in exclude:
            continue
        if asset_class(ticker) != cls:
            continue
        px = price_map.get(ticker)
        if px is None or pd.isna(px):
            continue
        avg = cost / qty
        unrealized = qty * (px - avg)
        if unrealized < 0:
            rows.append({"ticker": ticker, "qty": qty, "avg_cost": avg,
                         "price": px, "unrealized_pnl": unrealized,
                         "max_loss": unrealized})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("unrealized_pnl").reset_index(drop=True)


def harvest(target_loss: float, losers: pd.DataFrame) -> list[dict]:
    """Greedy: walk losers worst-first. If the full position's loss is still
    less than what's needed, take all and continue. If the full position would
    overshoot (more loss than needed), take a partial (integer shares).
    `target_loss` and `remaining` are negative values to reach toward zero.
    """
    plan = []
    remaining = target_loss
    for _, r in losers.iterrows():
        if remaining >= -1.0:
            break
        max_loss = r["max_loss"]  # negative, full position
        # max_loss > remaining means less-negative → full position doesn't
        # reach target; take it all and keep going.
        if max_loss > remaining:
            plan.append({"ticker": r["ticker"], "qty": r["qty"],
                         "avg_cost": r["avg_cost"], "price": r["price"],
                         "realized_loss": max_loss, "partial": False})
            remaining -= max_loss
            continue
        # Full position would overshoot → partial.
        loss_per_share = r["price"] - r["avg_cost"]  # negative
        if loss_per_share >= 0:
            continue  # safety
        need_qty = remaining / loss_per_share
        qty = max(1, math.floor(need_qty))
        qty = min(qty, int(r["qty"]))
        if qty < 1:
            continue  # fractional position with <1 share available
        realized = qty * loss_per_share
        plan.append({"ticker": r["ticker"], "qty": float(qty),
                     "avg_cost": r["avg_cost"], "price": r["price"],
                     "realized_loss": realized, "partial": True})
        remaining -= realized
        break
    return plan


def render_report(sells_realized: list[dict], carry: dict, mtd: dict,
                  plan_acoes: list[dict], plan_fii: list[dict],
                  decision: dict) -> str:
    today = date.today().isoformat()
    lines: list[str] = []
    w = lines.append

    w(f"# TLH plan — {today}\n")
    w(f"**Gerado**: {today}")
    w(f"**Mês de apuração**: {date.today().strftime('%m/%Y')}\n")
    w("> ⚠ **Não é orientação fiscal nem ordem de execução.** Use como guia para "
      "decisão; confirme cotações no momento da execução.\n")

    w("\n## Situação do mês (antes das vendas propostas)\n")
    w("| Bucket | Vendas brutas MTD | PnL realizado MTD | Prejuízo acumulado |")
    w("|---|---|---|---|")
    for cls in ("AÇÃO", "FII"):
        w(f"| {cls} | R$ {mtd[cls]['gross']:,.2f} | R$ {mtd[cls]['pnl']:+,.2f} "
          f"| R$ {carry[cls]:,.2f} |")

    w("\n## Vendas alvo (propostas)\n")
    if not sells_realized:
        w("Nenhuma venda válida informada.")
        return "\n".join(lines)
    w("| Ticker | Qtde | PM | Preço atual | Sell value | PnL realizado | Bucket |")
    w("|---|---|---|---|---|---|---|")
    for s in sells_realized:
        w(f"| {s['ticker']} | {s['qty']:g} | R$ {s['avg_cost']:.4f} "
          f"| R$ {s['price']:.4f} | R$ {s['sell_value']:,.2f} "
          f"| R$ {s['pnl']:+,.2f} | {s['cls']} |")

    # --- Decisão por bucket ---
    w("\n## Análise & recomendação\n")
    for cls in ("AÇÃO", "FII"):
        d = decision.get(cls)
        if d is None:
            continue
        w(f"\n### {cls}\n")
        w(f"- Vendas brutas pós-proposta: **R$ {d['gross_after']:,.2f}**")
        w(f"- PnL pós-proposta (antes de TLH): **R$ {d['pnl_after']:+,.2f}**")
        w(f"- Prejuízo acumulado disponível: R$ {carry[cls]:,.2f}")
        w(f"- Status: **{d['status']}**")
        w(f"- {d['rationale']}")
        if d["status"] == "TLH recomendado" and d["target_loss"]:
            w(f"- Alvo de perdas a realizar: **R$ {d['target_loss']:+,.2f}**")

    # --- Plano de execução ---
    plans = [("AÇÃO", plan_acoes), ("FII", plan_fii)]
    any_plan = any(p for _, p in plans)
    if any_plan:
        w("\n## Execução\n")
        w("### HOJE — vendas\n")
        w("Vender hoje (na ordem; pode quebrar em ordens menores se houver "
          "iliquidez intraday):\n")
        w("| # | Ticker | Qtde | Preço ref | Realizado |")
        w("|---|---|---|---|---|")
        i = 1
        all_losers: list[dict] = []
        for cls, plan in plans:
            for p in plan:
                tag = " (parcial)" if p["partial"] else ""
                w(f"| {i} | **{p['ticker']}**{tag} | {p['qty']:g} "
                  f"| R$ {p['price']:.4f} | R$ {p['realized_loss']:+,.2f} |")
                i += 1
                all_losers.append(p)
        w("\nE as vendas-alvo já listadas acima na seção 'Vendas alvo'.\n")

        w("\n### T+1 (próximo pregão) — recompra dos losers\n")
        w("> ⚠ **NÃO recomprar no mesmo dia da venda** — qualquer cruzamento "
          "intraday é classificado como day-trade pela B3 (20% sem isenção, "
          "bucket separado). Recompre apenas no pregão seguinte.\n")
        w("| Ticker | Qtde | Comentário |")
        w("|---|---|---|")
        for p in all_losers:
            w(f"| {p['ticker']} | {p['qty']:g} | recompra ~ preço de mercado "
              f"do dia (custo médio será resetado para o novo preço) |")

    w("\n## Resultado esperado pós-execução\n")
    w("| Bucket | Gross final | PnL líquido | Carry usado | IR esperado |")
    w("|---|---|---|---|---|")
    for cls in ("AÇÃO", "FII"):
        d = decision.get(cls)
        if d is None:
            continue
        w(f"| {cls} | R$ {d['final_gross']:,.2f} | R$ {d['final_pnl']:+,.2f} "
          f"| R$ {d['carry_used']:,.2f} | R$ {d['ir']:,.2f} |")

    w("\n## Avisos\n")
    w("- **Slippage T→T+1**: preços podem se mover entre venda e recompra. Em "
      "papéis voláteis a perda pode virar lucro (ou aumentar). Considere "
      "fracionar a recompra ao longo do dia seguinte.")
    w("- **Cost basis reset**: ao realizar perda + recomprar, o novo PM é o "
      "preço de recompra. Você perde o PM antigo (preço de aquisição "
      "histórico). Para fins de IRPF, a posição em 31/12 vai refletir o novo PM.")
    w("- **Day-trade**: jamais cruzar (sell+buy do mesmo ticker no mesmo "
      "pregão). Inclui ordens parciais que se sobrepõem intraday.")
    w("- **IRRF 0,005%** sobre vendas (\"dedo-duro\") incide normalmente; "
      "deduzível do IR devido no fim do mês.")
    w("- **AÇÕES e FIIs são buckets separados**: nunca cruzar compensação "
      "entre os dois.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exclude", default="",
                   help="Tickers a NÃO sugerir como losers (CSV).")
    p.add_argument("--no-save", action="store_true",
                   help="Não escreve o relatório em reports/.")
    p.add_argument("--sells", default="",
                   help="Vendas não-interativas: 'TICKER:QTDE,TICKER:QTDE'.")
    p.add_argument("--sell", action="append", default=[], metavar="TICKER:QTDE",
                   help="Uma venda (repetível). Alternativa a --sells.")
    p.add_argument("--json", action="store_true",
                   help="Emite o plano como JSON no stdout (em vez do markdown; "
                        "nunca salva o relatório).")
    args = p.parse_args(argv[1:])
    exclude = {t.strip().upper() for t in args.exclude.split(",") if t.strip()}
    sells_spec = ([args.sells] if args.sells else []) + args.sell

    if not POSITIONS.exists():
        print("Nenhuma posição. Rode load_b3 antes.", file=sys.stderr)
        return 1

    tl = build_timeline()
    carry, mtd = walk_history(tl)
    pm_now = load_positions()
    prices = latest_prices()
    if prices.empty:
        print("⚠ Sem cotações disponíveis. Rode fetch_fundamentus.py.", file=sys.stderr)
        return 1

    if not args.json:
        print(f"\n=== TLH plan — {date.today().isoformat()} ===\n")
        print(f"Mês: {date.today().strftime('%m/%Y')}\n")
        print(f"Situação MTD:")
        for cls in ("AÇÃO", "FII"):
            print(f"  {cls}: gross R$ {mtd[cls]['gross']:>14,.2f} | "
                  f"PnL R$ {mtd[cls]['pnl']:>+12,.2f} | "
                  f"carry R$ {carry[cls]:>12,.2f}")

    # Sells source: explicit flags (non-interactive) > --json (situation only) >
    # interactive prompt (unchanged default).
    if sells_spec:
        try:
            sells = parse_sells(sells_spec, pm_now)
        except ValueError as e:
            print(f"⚠ Venda inválida: {e}", file=sys.stderr)
            return 2
    elif args.json:
        sells = []  # never block on stdin in machine mode
    else:
        sells = prompt_sells(pm_now)
    if not sells and not args.json:
        print("\nNenhuma venda informada. Saindo.")
        return 0

    sells_realized = realize(sells, pm_now, prices)
    if sells and not sells_realized:
        return 1

    # --- Decide per bucket ---
    decision: dict[str, dict] = {}
    plan_acoes: list[dict] = []
    plan_fii: list[dict] = []

    for cls in ("AÇÃO", "FII"):
        rate = ALIQUOTA[cls]
        bucket_sells = [s for s in sells_realized if s["cls"] == cls]
        if not bucket_sells and mtd[cls]["gross"] == 0:
            continue  # nothing to plan for this bucket
        sell_gross = sum(s["sell_value"] for s in bucket_sells)
        sell_pnl = sum(s["pnl"] for s in bucket_sells)
        gross_after = mtd[cls]["gross"] + sell_gross
        pnl_after = mtd[cls]["pnl"] + sell_pnl
        c = carry[cls]

        # No-op cases
        if cls == "AÇÃO" and gross_after <= ISENCAO_LIMIT:
            decision[cls] = {
                "gross_after": gross_after, "pnl_after": pnl_after,
                "status": "Nenhum TLH necessário",
                "rationale": (f"Vendas brutas do mês ({gross_after:,.2f}) ≤ "
                              f"R$ 20.000 → mês isento. Lucro vai para "
                              f"Rendimentos Isentos código 20 no IRPF."),
                "target_loss": 0.0, "carry_used": 0.0,
                "final_gross": gross_after, "final_pnl": pnl_after, "ir": 0.0,
            }
            continue
        if pnl_after <= 0:
            decision[cls] = {
                "gross_after": gross_after, "pnl_after": pnl_after,
                "status": "Nenhum TLH necessário",
                "rationale": ("Mês já tem PnL ≤ 0 — nada a tributar "
                              "(prejuízo vai pro carry)."),
                "target_loss": 0.0, "carry_used": 0.0,
                "final_gross": gross_after, "final_pnl": pnl_after, "ir": 0.0,
            }
            continue
        if c >= pnl_after:
            decision[cls] = {
                "gross_after": gross_after, "pnl_after": pnl_after,
                "status": "Nenhum TLH necessário",
                "rationale": (f"Prejuízo acumulado (R$ {c:,.2f}) já cobre o "
                              f"ganho do mês (R$ {pnl_after:,.2f}). Carry "
                              f"consumido absorve tudo."),
                "target_loss": 0.0, "carry_used": pnl_after,
                "final_gross": gross_after, "final_pnl": pnl_after, "ir": 0.0,
            }
            continue

        # TLH needed: need to harvest enough loss to bring (pnl_after - carry)
        # down to zero. Target loss = -(pnl_after - c).
        residual = pnl_after - c
        target_loss = -residual
        losers = find_losers(pm_now, prices, cls,
                             exclude | {s["ticker"] for s in bucket_sells})
        if losers.empty:
            decision[cls] = {
                "gross_after": gross_after, "pnl_after": pnl_after,
                "status": "TLH desejável mas sem candidatos",
                "rationale": (f"Sem posições em prejuízo no bucket {cls} "
                              f"que cubram R$ {residual:,.2f}. IR estimado: "
                              f"R$ {residual * rate:,.2f}."),
                "target_loss": target_loss, "carry_used": c,
                "final_gross": gross_after, "final_pnl": pnl_after,
                "ir": residual * rate,
            }
            continue
        plan = harvest(target_loss, losers)
        harvested = sum(p["realized_loss"] for p in plan)
        final_pnl = pnl_after + harvested
        # carry available consumes remaining positive PnL if any
        carry_used = min(c, max(0.0, final_pnl))
        final_taxable = max(0.0, final_pnl - carry_used)
        ir = final_taxable * rate
        gross_after_total = gross_after + sum(p["price"] * p["qty"] for p in plan)
        decision[cls] = {
            "gross_after": gross_after, "pnl_after": pnl_after,
            "status": "TLH recomendado",
            "rationale": (f"Ganho residual após carry: R$ {residual:,.2f}. "
                          f"Plano realiza R$ {harvested:+,.2f} em perdas, "
                          f"levando PnL final a R$ {final_pnl:+,.2f}."),
            "target_loss": target_loss, "carry_used": carry_used,
            "final_gross": gross_after_total, "final_pnl": final_pnl,
            "ir": ir,
        }
        if cls == "AÇÃO":
            plan_acoes = plan
        else:
            plan_fii = plan

    if args.json:
        result = build_result(sells_realized, carry, mtd,
                              plan_acoes, plan_fii, decision)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    report = render_report(sells_realized, carry, mtd,
                           plan_acoes, plan_fii, decision)
    print()
    print(report)

    if not args.no_save:
        REPORTS.mkdir(exist_ok=True)
        stem = f"{date.today().isoformat()}-tlh-plan"
        out = REPORTS / f"{stem}.md"
        i = 2
        while out.exists():
            out = REPORTS / f"{stem}-{i}.md"
            i += 1
        out.write_text(report)
        print(f"\n💾 Relatório salvo em: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
