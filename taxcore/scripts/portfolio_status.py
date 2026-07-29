"""Show current portfolio: allocation, unrealized P&L, total return (incl. cash
income), and trailing-12mo yield-on-cost.

Usage:
    python -m scripts.portfolio_status
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from scripts._common import price_lookup

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "positions.csv"
TRADES = ROOT / "data" / "trades.csv"
CASH_EVENTS = ROOT / "data" / "cash_events.csv"


def per_ticker_history() -> pd.DataFrame:
    """Walk each ticker's full trade + cash event timeline and produce honest
    return metrics that don't get distorted by position trimming.

    Returns columns per ticker:
      total_invested      — sum of all buy values, gross (never reduced by sells)
      realized_pnl        — locked-in P&L from sells, using PM accounting
                            (sell_price - avg_cost_at_sell) × sold_qty
      cb_weighted_yield_pct — Σ (event_amount / cost_basis_at_event_time) × 100
                            i.e. cumulative cash yield experienced on the
                            capital actually deployed when each event was paid.
                            This is what a trimmed winner's "yield" really was
                            — not income_lifetime / current_cost.

    Combine with current unrealized to get a coherent total-return picture:
      total_return_pnl = realized_pnl + unrealized_pnl + cash_income_lifetime
      total_return_pct = total_return_pnl / total_invested × 100
    """
    if not TRADES.exists():
        return pd.DataFrame(columns=[
            "ticker", "total_invested", "realized_pnl", "cb_weighted_yield_pct"])
    trades = pd.read_csv(TRADES, parse_dates=["date"])
    if trades.empty:
        return pd.DataFrame(columns=[
            "ticker", "total_invested", "realized_pnl", "cb_weighted_yield_pct"])

    # Equity-relevant only (skip options, Renda Fixa). Strip *F so fractional
    # and round-lot trades roll up to the same canonical ticker — same rule
    # used by load_b3.consolidate_fractional and the price-merge logic.
    equity_pat = r"Variável|à Vista|Vista|Fracionário"
    trades = trades[trades["market"].str.contains(
        equity_pat, case=False, na=False, regex=True)].copy()
    trades["ticker"] = trades["ticker"].str.replace(r"F$", "", regex=True)

    # Cash events already use canonical tickers (read_movimentacao extracts
    # them from the 'TICKER - DESCRIPTION' Produto cell with a regex that
    # rejects the F suffix).
    if CASH_EVENTS.exists():
        events = pd.read_csv(CASH_EVENTS, parse_dates=["date"])
    else:
        events = pd.DataFrame(columns=["date", "ticker", "amount", "is_amortization"])

    # Build a unified timeline. Kind ∈ {buy, sell, income, amort}; payload is
    # always (qty, amount) — qty=0 for cash events, amount = qty*price for trades.
    rows: list[dict] = []
    for _, t in trades.iterrows():
        is_buy = str(t["side"]).lower().startswith("compra")
        rows.append({
            "ticker": t["ticker"], "date": t["date"],
            "kind": "buy" if is_buy else "sell",
            "qty": float(t["qty"]),
            "amount": float(t["qty"]) * float(t["price"]),
        })
    for _, e in events.iterrows():
        amt = float(e["amount"]) if pd.notna(e["amount"]) else 0.0
        # Map event_category to the per_ticker_history walker's kinds.
        # qty_events and reembolso/other rows are skipped: qty changes are
        # already baked into positions.csv via load_b3.derive_positions, and
        # reembolso is reported separately. Amortizations reduce cost basis;
        # only the income category contributes to attrib_yield.
        cat = e.get("event_category")
        if isinstance(cat, str):
            if cat == "amortization":
                kind = "amort"
            elif cat == "income":
                kind = "income"
            else:
                continue  # reembolso / qty_event / other
        else:
            # Legacy csv: only is_amortization was tracked; treat the rest
            # as income (the old behavior). Reembolso filtering is unavailable.
            kind = "amort" if bool(e.get("is_amortization", False)) else "income"
        rows.append({
            "ticker": str(e["ticker"]),
            "date": e["date"],
            "kind": kind,
            "qty": 0.0,
            "amount": amt,
        })
    timeline = pd.DataFrame(rows).dropna(subset=["date", "ticker"])
    timeline = timeline.sort_values(["ticker", "date"]).reset_index(drop=True)

    out: list[dict] = []
    for ticker, group in timeline.groupby("ticker"):
        invested = 0.0
        realized = 0.0
        cost = 0.0
        qty = 0.0
        attrib_yield = 0.0
        for _, r in group.iterrows():
            if r["kind"] == "buy":
                invested += r["amount"]
                cost += r["amount"]
                qty += r["qty"]
            elif r["kind"] == "sell":
                if qty > 0:
                    avg = cost / qty
                    sell_qty = min(r["qty"], qty)  # guard against orphan oversell
                    sell_price = r["amount"] / r["qty"] if r["qty"] else 0.0
                    realized += sell_qty * (sell_price - avg)
                    cost -= sell_qty * avg
                    qty -= sell_qty
                # else: orphan sell with no prior inventory — incomplete history,
                # skip without polluting realized P&L.
            elif r["kind"] == "amort":
                # Amortization returns principal: reduces cost basis, doesn't
                # count toward yield.
                cost = max(0.0, cost - r["amount"])
            else:  # income
                if cost > 0:
                    attrib_yield += r["amount"] / cost
                # If cost == 0 (e.g. fully sold then ex-date dividend trickling
                # in), skip — this is a windfall on a closed position, not yield
                # on deployed capital.
        out.append({
            "ticker": ticker,
            "total_invested": round(invested, 2),
            "realized_pnl": round(realized, 2),
            "cb_weighted_yield_pct": round(attrib_yield * 100, 2),
        })
    return pd.DataFrame(out)


def cash_income_per_ticker() -> pd.DataFrame:
    """Aggregate cash_events.csv into per-ticker income totals.

    Only event_category == "income" is summed — that's Dividendo, JCP, and
    Rendimento (FII/CRI distributions). Reembolso, Amortização, qty_events
    (splits/bonificações), and "other" cash flows (Leilão de Fração, etc.)
    are intentionally excluded so income totals reflect actual investment
    income on deployed capital.

    Returns columns:
      ticker, income_lifetime, income_ttm
    where income_ttm covers the trailing 12 months ending at the latest
    income event date.
    """
    cols = ["ticker", "income_lifetime", "income_ttm"]
    if not CASH_EVENTS.exists():
        return pd.DataFrame(columns=cols)
    ce = pd.read_csv(CASH_EVENTS, parse_dates=["date"])
    if ce.empty:
        return pd.DataFrame(columns=cols)
    # Filter to investment-income rows. Fall back to the legacy
    # is_amortization-only filter for older cash_events.csv files that
    # predate the event_category column.
    if "event_category" in ce.columns:
        ce = ce[ce["event_category"] == "income"]
    else:
        ce = ce[~ce["is_amortization"].fillna(False)]
    if ce.empty:
        return pd.DataFrame(columns=cols)
    # Anchor TTM to the latest event date so we don't miss recent activity
    # because of clock skew between report download and "today".
    cutoff = ce["date"].max() - pd.Timedelta(days=365)
    ttm = ce[ce["date"] >= cutoff]
    lifetime = ce.groupby("ticker")["amount"].sum().rename("income_lifetime")
    ttm_per = ttm.groupby("ticker")["amount"].sum().rename("income_ttm")
    out = pd.concat([lifetime, ttm_per], axis=1).fillna(0.0).reset_index()
    return out


def premium_totals() -> tuple[float, float]:
    """Net option-writing premium (lifetime, trailing 12 months).

    Premium events are signed: sells are +, buys are -, so the sum is realized
    P&L from option premiums. Reported as a separate headline line because
    option writing is not dividend income and per-ticker attribution is messy
    (option-series tickers like 'ITSA' don't match position tickers 'ITSA4').
    """
    if not CASH_EVENTS.exists():
        return 0.0, 0.0
    ce = pd.read_csv(CASH_EVENTS, parse_dates=["date"])
    if ce.empty or "event_category" not in ce.columns:
        return 0.0, 0.0
    prem = ce[ce["event_category"] == "premium"]
    if prem.empty:
        return 0.0, 0.0
    lifetime = float(prem["amount"].sum())
    latest = prem["date"].max()
    ttm_cutoff = latest - pd.DateOffset(years=1)
    ttm = float(prem.loc[prem["date"] >= ttm_cutoff, "amount"].sum())
    return lifetime, ttm


def reembolso_per_ticker() -> pd.DataFrame:
    """Aggregate per-ticker Reembolso totals from cash_events.csv.

    Reembolsos are broker / withholding refunds — real cash but not
    investment income. Reported on a separate line in the headline so the
    user can see the magnitude without it inflating dividend yields.
    """
    cols = ["ticker", "reembolso_lifetime"]
    if not CASH_EVENTS.exists():
        return pd.DataFrame(columns=cols)
    ce = pd.read_csv(CASH_EVENTS, parse_dates=["date"])
    if ce.empty or "event_category" not in ce.columns:
        return pd.DataFrame(columns=cols)
    ce = ce[ce["event_category"] == "reembolso"]
    if ce.empty:
        return pd.DataFrame(columns=cols)
    out = ce.groupby("ticker")["amount"].sum().rename("reembolso_lifetime").reset_index()
    return out


def main() -> int:
    if not POSITIONS.exists():
        print("No positions yet. Run load_b3.py first.", file=sys.stderr)
        return 1
    pos = pd.read_csv(POSITIONS)
    if pos.empty:
        print("Positions file is empty.", file=sys.stderr)
        return 0

    # Build a single 'ticker → price' lookup table from all available sources:
    # equity screener, FII screener, and an optional yfinance fallback for
    # tickers neither Fundamentus page covers (e.g. BDR-ETFs like IVVB11).
    # If a ticker appears in multiple sources, the equity screener wins.
    prices = price_lookup()

    df = pos.merge(prices, on="ticker", how="left")

    # Bring in lifetime + TTM cash income per ticker. Missing tickers (no
    # historical events) get 0; correct for newly opened positions.
    income = cash_income_per_ticker()
    df = df.merge(income, on="ticker", how="left")
    df[["income_lifetime", "income_ttm"]] = df[["income_lifetime", "income_ttm"]].fillna(0.0)

    # Reembolsos are tracked separately so they don't inflate income totals
    # but stay visible in the headline.
    reemb = reembolso_per_ticker()
    df = df.merge(reemb, on="ticker", how="left")
    df["reembolso_lifetime"] = df["reembolso_lifetime"].fillna(0.0)

    # Per-ticker history-derived metrics: realized P&L, gross capital
    # invested, cost-basis-weighted yield. Replaces the naive total_ret_%
    # which inflated for trimmed winners (income / current_cost when most
    # income was paid on a much larger past holding).
    history = per_ticker_history()
    df = df.merge(history, on="ticker", how="left")
    df[["total_invested", "realized_pnl", "cb_weighted_yield_pct"]] = (
        df[["total_invested", "realized_pnl", "cb_weighted_yield_pct"]].fillna(0.0)
    )
    # Fallback: if a ticker has no trade history (e.g. transferred in from
    # another broker), use current cost basis as the invested-capital proxy.
    df["total_invested"] = df["total_invested"].where(
        df["total_invested"] > 0, df["total_cost"])

    df["market_value"] = df["qty"] * df["price"]
    df["unrealized_pnl"] = df["market_value"] - df["total_cost"]
    df["unrealized_pct"] = (df["unrealized_pnl"] / df["total_cost"] * 100).round(2)
    # Honest total return: realized P&L (locked in by past sells) + unrealized
    # (current price vs current cost) + cash income. Denominator is gross
    # capital ever invested in this name, so trimmed winners no longer claim
    # multi-hundred-percent returns from income paid on positions that no
    # longer exist.
    df["total_return_pnl"] = (
        df["realized_pnl"] + df["unrealized_pnl"] + df["income_lifetime"])
    df["total_return_pct"] = (
        df["total_return_pnl"] / df["total_invested"] * 100).round(2)
    # Yield-on-cost: trailing-12mo income as % of current cost basis. For
    # positions held less than a year, income_ttm understates the run-rate.
    df["yoc_ttm_pct"] = (df["income_ttm"] / df["total_cost"] * 100).round(2)

    priced = df[df["price"].notna()].copy()
    unpriced = df[df["price"].isna()].copy()

    total_mv = priced["market_value"].sum()
    total_cost_priced = priced["total_cost"].sum()
    total_cost_unpriced = unpriced["total_cost"].sum()
    total_invested_priced = priced["total_invested"].sum()
    total_realized_priced = priced["realized_pnl"].sum()
    total_income_priced = priced["income_lifetime"].sum()
    total_income_ttm_priced = priced["income_ttm"].sum()
    total_reembolso_priced = priced["reembolso_lifetime"].sum()
    # Include reembolsos paid on closed positions (no longer in df) so the
    # headline reflects total cash refunded over the user's history.
    all_reembolso_df = reembolso_per_ticker()
    total_reembolso_all = (
        float(all_reembolso_df["reembolso_lifetime"].sum())
        if not all_reembolso_df.empty else 0.0
    )
    priced["weight_pct"] = (priced["market_value"] / total_mv * 100).round(2)

    priced = priced.sort_values("market_value", ascending=False).reset_index(drop=True)
    show = priced[[
        "ticker", "qty", "avg_cost", "price", "market_value", "weight_pct",
        "unrealized_pct", "income_ttm", "yoc_ttm_pct",
        "income_lifetime", "total_return_pct",
    ]].copy()
    for c in ("avg_cost", "price", "market_value", "income_ttm", "income_lifetime"):
        show[c] = show[c].round(2)
    show = show.rename(columns={
        "income_ttm": "income_12m",
        "yoc_ttm_pct": "yoc_12m_%",
        "income_lifetime": "income_life",
        "unrealized_pct": "unrealiz_%",
        "total_return_pct": "total_ret_%",
    })

    print("=== Priced positions (equity + FII + external prices) ===")
    print(tabulate(show, headers="keys", tablefmt="github", showindex=False))
    print()
    print(f"Market value:           R$ {total_mv:>13,.2f}")
    print(f"Current cost basis:     R$ {total_cost_priced:>13,.2f}")
    print(f"Gross invested:         R$ {total_invested_priced:>13,.2f}  "
          f"(buys-only; sells did not refund this)")
    print(f"Realized P&L (sells):   R$ {total_realized_priced:>13,.2f}")
    print(f"Unrealized:             R$ {total_mv - total_cost_priced:>13,.2f}  "
          f"({(total_mv - total_cost_priced) / total_cost_priced * 100:+.2f}% on current cost)")
    print(f"Cash income (lifetime): R$ {total_income_priced:>13,.2f}  "
          f"(Dividendo + JCP + Rendimento only)")
    print(f"Cash income (last 12m): R$ {total_income_ttm_priced:>13,.2f}  "
          f"(YoC {total_income_ttm_priced / total_cost_priced * 100:+.2f}% on current cost)")
    if total_reembolso_all > 0:
        extra = ""
        if total_reembolso_all - total_reembolso_priced > 1e-2:
            extra = (f"  (incl. R$ {total_reembolso_all - total_reembolso_priced:,.2f} "
                     f"on closed/unpriced positions)")
        print(f"Reembolsos (lifetime):  R$ {total_reembolso_priced:>13,.2f}  "
              f"(broker / withholding refunds, not income){extra}")
    premium_lifetime, premium_ttm = premium_totals()
    if abs(premium_lifetime) > 1e-2:
        print(f"Option premium (life):  R$ {premium_lifetime:>13,.2f}  "
              f"(net realized; sells − buys across all option trades)")
        if abs(premium_ttm) > 1e-2:
            print(f"Option premium (12m):   R$ {premium_ttm:>13,.2f}")
    total_ret = (total_realized_priced + (total_mv - total_cost_priced)
                 + total_income_priced + premium_lifetime)
    denom = total_invested_priced if total_invested_priced > 0 else total_cost_priced
    print(f"Total return:           R$ {total_ret:>13,.2f}  "
          f"({total_ret / denom * 100:+.2f}% on gross invested)")

    if not unpriced.empty:
        u = unpriced.sort_values("total_cost", ascending=False).reset_index(drop=True)
        u_show = u[["ticker", "qty", "avg_cost", "total_cost",
                    "income_lifetime"]].copy()
        for c in ("avg_cost", "total_cost", "income_lifetime"):
            u_show[c] = u_show[c].round(2)
        print()
        print("=== Unpriced positions (no source returned a price) ===")
        print(tabulate(u_show, headers="keys", tablefmt="github", showindex=False))
        print()
        print(f"Unpriced cost basis:      R$ {total_cost_unpriced:>13,.2f}")
        print(f"Unpriced lifetime income: R$ {unpriced['income_lifetime'].sum():>13,.2f}")
        print("⚠️  Tried Fundamentus equity, Fundamentus FII, and yfinance — none returned a price. "
              "Most likely cause: ticker is delisted, suspended, or trades under a successor symbol "
              "after a corporate action. Check your broker's custody view to confirm what you actually hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
