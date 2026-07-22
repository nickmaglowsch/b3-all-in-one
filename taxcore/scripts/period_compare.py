"""Compare portfolio performance across two date ranges, vs IBOV.

Splits the trade + cash-event history at a user-chosen cutoff date and
computes realized P&L, dividends, and (for the post-cutoff period only)
current unrealized P&L on still-held positions. Reports against the IBOV
price return over each window.

The walker uses average-cost accounting (preço médio). For positions that
straddle the cutoff, the pre-cutoff sub-walk produces a (qty, cost_basis)
snapshot at the boundary, which the post-cutoff walk inherits as its
starting state. Realized P&L is attributed to the period in which the
sell happened — using the avg-cost computed from all buys up to that
moment (which is the same convention Receita Federal uses).

Limitation: unrealized gain that accrued during the pre-cutoff period on a
position that's still held today CANNOT be separated from post-cutoff
unrealized without historical prices at the cutoff. We attribute all
current unrealized to the post-cutoff period and flag this as a caveat.

Usage:
    python -m scripts.period_compare --cutoff 2023-08-18
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from scripts._common import annualize, price_lookup as _price_lookup

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TRADES_CSV = DATA_DIR / "trades.csv"
CASH_EVENTS_CSV = DATA_DIR / "cash_events.csv"
POSITIONS_CSV = DATA_DIR / "positions.csv"


def split_history(cutoff: pd.Timestamp) -> dict:
    """Walk each ticker through the timeline and split metrics at `cutoff`.

    Returns a dict with pre/post breakdowns:
      pre_realized, pre_income, pre_premium, pre_invested
      post_realized, post_income, post_premium, post_invested
      current_unrealized (attributed to post period)
    """
    trades = pd.read_csv(TRADES_CSV, parse_dates=["date"])
    equity_pat = r"Variável|à Vista|Vista|Fracionário"
    trades = trades[trades["market"].str.contains(
        equity_pat, case=False, na=False, regex=True)].copy()
    trades["ticker"] = trades["ticker"].str.replace(r"F$", "", regex=True)

    events = pd.read_csv(CASH_EVENTS_CSV, parse_dates=["date"])

    # Build unified per-ticker timeline.
    rows = []
    for _, t in trades.iterrows():
        is_buy = str(t["side"]).lower().startswith("compra")
        rows.append({
            "ticker": t["ticker"], "date": t["date"],
            "kind": "buy" if is_buy else "sell",
            "qty": float(t["qty"]),
            "amount": float(t["qty"]) * float(t["price"]),
        })
    for _, e in events.iterrows():
        cat = e.get("event_category")
        amt = float(e["amount"]) if pd.notna(e["amount"]) else 0.0
        if cat == "income":
            kind = "income"
        elif cat == "amortization":
            kind = "amort"
        elif cat == "premium":
            kind = "premium"
        else:
            continue
        rows.append({"ticker": str(e["ticker"]), "date": e["date"],
                     "kind": kind, "qty": 0.0, "amount": amt})

    tl = pd.DataFrame(rows).dropna(subset=["date", "ticker"])
    tl = tl.sort_values(["ticker", "date"]).reset_index(drop=True)

    metrics = {
        "pre_realized": 0.0, "pre_income": 0.0, "pre_premium": 0.0,
        "pre_invested": 0.0,
        "post_realized": 0.0, "post_income": 0.0, "post_premium": 0.0,
        "post_invested": 0.0,
        "per_ticker": [],
    }

    cost_at_cutoff_total = 0.0
    for ticker, group in tl.groupby("ticker"):
        qty = 0.0
        cost = 0.0
        cutoff_qty = 0.0
        cutoff_cost = 0.0
        cutoff_snapped = False
        # Per-ticker accumulators by period.
        t_pre_real = t_post_real = 0.0
        t_pre_inc = t_post_inc = 0.0
        t_pre_prem = t_post_prem = 0.0
        t_pre_inv = t_post_inv = 0.0
        for _, r in group.iterrows():
            # Snap (qty, cost) the first time we cross into the post-period
            # for this ticker. Captures what the user was carrying at cutoff.
            if not cutoff_snapped and r["date"] >= cutoff:
                cutoff_qty = qty
                cutoff_cost = cost
                cutoff_snapped = True
            pre = r["date"] < cutoff
            if r["kind"] == "buy":
                cost += r["amount"]
                qty += r["qty"]
                if pre:
                    t_pre_inv += r["amount"]
                else:
                    t_post_inv += r["amount"]
            elif r["kind"] == "sell":
                if qty > 0:
                    avg = cost / qty
                    sell_qty = min(r["qty"], qty)
                    sell_px = r["amount"] / r["qty"] if r["qty"] else 0.0
                    pnl = sell_qty * (sell_px - avg)
                    cost -= sell_qty * avg
                    qty -= sell_qty
                    if pre:
                        t_pre_real += pnl
                    else:
                        t_post_real += pnl
            elif r["kind"] == "income":
                if pre:
                    t_pre_inc += r["amount"]
                else:
                    t_post_inc += r["amount"]
            elif r["kind"] == "premium":
                # Premium is per-contract; ticker is option-series code.
                # We still attribute by date.
                if pre:
                    t_pre_prem += r["amount"]
                else:
                    t_post_prem += r["amount"]
            elif r["kind"] == "amort":
                cost = max(0.0, cost - r["amount"])
        # If the ticker never had any post-cutoff event, snap-on-cross
        # never fired — its cost basis at cutoff equals its end-of-walk cost.
        if not cutoff_snapped:
            cutoff_qty = qty
            cutoff_cost = cost
        cost_at_cutoff_total += cutoff_cost
        metrics["pre_realized"] += t_pre_real
        metrics["pre_income"] += t_pre_inc
        metrics["pre_premium"] += t_pre_prem
        metrics["pre_invested"] += t_pre_inv
        metrics["post_realized"] += t_post_real
        metrics["post_income"] += t_post_inc
        metrics["post_premium"] += t_post_prem
        metrics["post_invested"] += t_post_inv
        metrics["per_ticker"].append({
            "ticker": ticker,
            "pre_realized": round(t_pre_real, 2),
            "post_realized": round(t_post_real, 2),
            "pre_income": round(t_pre_inc, 2),
            "post_income": round(t_post_inc, 2),
            "pre_invested": round(t_pre_inv, 2),
            "post_invested": round(t_post_inv, 2),
            "end_qty": round(qty, 4),
            "end_cost": round(cost, 2),
        })

    # Current unrealized: attribute to post-period.
    pos = pd.read_csv(POSITIONS_CSV)
    prices = _price_lookup()
    df = pos.merge(prices, on="ticker", how="left")
    df["market_value"] = df["qty"] * df["price"]
    priced = df[df["price"].notna()]
    metrics["current_mv"] = float(priced["market_value"].sum())
    metrics["current_cost"] = float(priced["total_cost"].sum())
    metrics["current_unrealized"] = (
        metrics["current_mv"] - metrics["current_cost"]
    )
    metrics["n_priced"] = int(len(priced))
    metrics["n_unpriced"] = int(len(df) - len(priced))
    metrics["cost_at_cutoff"] = cost_at_cutoff_total
    return metrics


def fetch_ibov(start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float, float]:
    """Return (return_pct, first_close, last_close) for ^BVSP over [start, end]."""
    import yfinance as yf
    ibov = yf.download(
        "^BVSP",
        start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
        progress=False, auto_adjust=False,
    )
    if isinstance(ibov.columns, pd.MultiIndex):
        closes = ibov["Close"].iloc[:, 0]
    else:
        closes = ibov["Close"]
    closes = closes.dropna()
    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    return (last - first) / first * 100.0, first, last


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cutoff", required=True,
                   help="Boundary date YYYY-MM-DD (events on or after = post)")
    p.add_argument("--start", default=None,
                   help="Override period start (default: earliest trade date)")
    p.add_argument("--end", default=None,
                   help="Override period end (default: today)")
    args = p.parse_args(argv[1:])

    cutoff = pd.Timestamp(args.cutoff)
    trades = pd.read_csv(TRADES_CSV, parse_dates=["date"])
    start = pd.Timestamp(args.start) if args.start else trades["date"].min()
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    metrics = split_history(cutoff)

    # IBOV slices.
    ibov_pre_pct, ibov_pre_a, ibov_pre_b = fetch_ibov(start, cutoff)
    ibov_post_pct, ibov_post_a, ibov_post_b = fetch_ibov(cutoff, end)
    pre_years = (cutoff - start).days / 365.25
    post_years = (end - cutoff).days / 365.25

    pre_total = (metrics["pre_realized"] + metrics["pre_income"]
                 + metrics["pre_premium"])
    # Pre-period: use cost basis at cutoff as the "capital invested" proxy.
    # That's what the user was actually carrying through the period — gross
    # buys overstates because capital was recycled in and out.
    pre_capital = metrics["cost_at_cutoff"]
    pre_pct = (pre_total / pre_capital * 100.0
               if pre_capital > 0 else float("nan"))

    post_total = (metrics["post_realized"] + metrics["post_income"]
                  + metrics["post_premium"] + metrics["current_unrealized"])
    # Post-period: capital under management = current cost basis. This
    # treats inherited positions at their cost (the user's pre-CFA basis)
    # plus any new buys that are still held.
    post_capital = metrics["current_cost"]
    post_pct = (post_total / post_capital * 100.0
                if post_capital > 0 else float("nan"))

    print(f"Period split at cutoff: {cutoff.date()}")
    print(f"  Pre  : {start.date()} → {(cutoff - pd.Timedelta(days=1)).date()}  "
          f"({pre_years:.2f} yr)")
    print(f"  Post : {cutoff.date()} → {end.date()}  ({post_years:.2f} yr)")
    print()
    print("=" * 78)
    print(f"{'':30}   {'Pre (self-managed)':>22}   {'Post (CFA)':>16}")
    print("-" * 78)
    print(f"{'Realized P&L':30}   R$ {metrics['pre_realized']:>16,.2f}    "
          f"R$ {metrics['post_realized']:>12,.2f}")
    print(f"{'Cash income (div+JCP+rend)':30}   R$ {metrics['pre_income']:>16,.2f}    "
          f"R$ {metrics['post_income']:>12,.2f}")
    print(f"{'Option premium (net)':30}   R$ {metrics['pre_premium']:>16,.2f}    "
          f"R$ {metrics['post_premium']:>12,.2f}")
    print(f"{'Unrealized (current)':30}   {'—':>20}    "
          f"R$ {metrics['current_unrealized']:>12,.2f}")
    print("-" * 78)
    print(f"{'Period P&L':30}   R$ {pre_total:>16,.2f}    "
          f"R$ {post_total:>12,.2f}")
    print(f"{'Gross buys in period':30}   R$ {metrics['pre_invested']:>16,.2f}    "
          f"R$ {metrics['post_invested']:>12,.2f}")
    print(f"{'Capital invested':30}   R$ {pre_capital:>16,.2f}    "
          f"R$ {post_capital:>12,.2f}")
    print(f"{'  (basis: cost at cutoff / current cost basis)'}")
    print()
    print(f"{'Portfolio return':30}   {pre_pct:>+19.2f}%   {post_pct:>+15.2f}%")
    print(f"{'  annualized':30}   {annualize(pre_pct, pre_years):>+19.2f}%/yr "
          f"{annualize(post_pct, post_years):>+13.2f}%/yr")
    print()
    print(f"{'IBOV return (price-only)':30}   {ibov_pre_pct:>+19.2f}%   "
          f"{ibov_post_pct:>+15.2f}%")
    print(f"{'  annualized':30}   {annualize(ibov_pre_pct, pre_years):>+19.2f}%/yr "
          f"{annualize(ibov_post_pct, post_years):>+13.2f}%/yr")
    print()
    print(f"{'Alpha (port − IBOV)':30}   {pre_pct - ibov_pre_pct:>+19.2f} pp  "
          f"{post_pct - ibov_post_pct:>+15.2f} pp")
    print()
    print("Caveats:")
    print("  - IBOV is price-only (no dividends). Brazilian dividend yield is ~5%/yr;")
    print("    add roughly that × years to IBOV for a total-return comparison.")
    print("  - All current unrealized gain is attributed to the post-cutoff period.")
    print("    Some of that gain actually accrued during the pre-cutoff period on")
    print("    positions held continuously through the cutoff (notably IVVB11). To")
    print("    properly split, we'd need historical prices at the cutoff date.")
    print("  - Pre-period 'capital base' is gross buys; post-period uses the larger")
    print("    of (gross buys, current cost basis) to credit inherited capital.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
