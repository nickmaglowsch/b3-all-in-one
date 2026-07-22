"""Compare portfolio total return to IBOV (^BVSP) over the same period.

Computes a money-weighted-ish total return for the portfolio (realized P&L +
unrealized + cash income, on gross invested) and compares against the
price-only return of IBOV between the portfolio's first trade date and the
latest data date.

Usage:
    python -m scripts.portfolio_vs_ibov
    python -m scripts.portfolio_vs_ibov --start 2024-01-01
    python -m scripts.portfolio_vs_ibov --no-annualize
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from scripts._common import (
    EXTERNAL_PRICES,
    FII_SNAPSHOTS,
    SNAPSHOTS,
    annualize as _annualize,
    price_lookup as _build_price_lookup,
)
# Total-return metrics are computed exactly as portfolio_status.py does.
from scripts.portfolio_status import (
    cash_income_per_ticker,
    per_ticker_history,
)

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "positions.csv"
TRADES = ROOT / "data" / "trades.csv"


def _latest_data_date() -> pd.Timestamp:
    """Newest snapshot date across all price sources — this is our 'now'."""
    candidates: list[pd.Timestamp] = []
    for d in (SNAPSHOTS, FII_SNAPSHOTS, EXTERNAL_PRICES):
        if not d.exists():
            continue
        files = sorted(d.glob("*.csv"))
        if not files:
            continue
        # Filename is YYYY-MM-DD.csv; parse the stem.
        try:
            candidates.append(pd.Timestamp(files[-1].stem))
        except ValueError:
            continue
    if not candidates:
        return pd.Timestamp.today().normalize()
    return max(candidates)


def compute_portfolio_return(end_date: pd.Timestamp) -> dict:
    """Total portfolio return from inception through end_date.

    Mirrors the totals printed at the bottom of portfolio_status.py:
      total_pnl = realized + (mv - current_cost_priced) + cash_income_lifetime
      pct       = total_pnl / gross_invested * 100
    """
    if not POSITIONS.exists():
        raise SystemExit("No positions yet. Run load_b3.py first.")
    pos = pd.read_csv(POSITIONS)
    if pos.empty:
        print("No positions in data/positions.csv.")
        sys.exit(0)

    prices = _build_price_lookup()
    df = pos.merge(prices, on="ticker", how="left")

    income = cash_income_per_ticker()
    df = df.merge(income, on="ticker", how="left")
    df[["income_lifetime", "income_ttm"]] = df[
        ["income_lifetime", "income_ttm"]
    ].fillna(0.0)

    history = per_ticker_history()
    df = df.merge(history, on="ticker", how="left")
    df[["total_invested", "realized_pnl", "cb_weighted_yield_pct"]] = df[
        ["total_invested", "realized_pnl", "cb_weighted_yield_pct"]
    ].fillna(0.0)
    # Same fallback portfolio_status uses for tickers without trade history.
    df["total_invested"] = df["total_invested"].where(
        df["total_invested"] > 0, df["total_cost"]
    )

    df["market_value"] = df["qty"] * df["price"]
    priced = df[df["price"].notna()].copy()

    market_value = float(priced["market_value"].sum())
    current_cost_priced = float(priced["total_cost"].sum())
    gross_invested = float(priced["total_invested"].sum())
    realized_pnl = float(priced["realized_pnl"].sum())
    cash_income = float(priced["income_lifetime"].sum())

    unrealized = market_value - current_cost_priced
    total_pnl = realized_pnl + unrealized + cash_income
    denom = gross_invested if gross_invested > 0 else current_cost_priced
    return_pct = (total_pnl / denom * 100.0) if denom > 0 else float("nan")

    return {
        "market_value": market_value,
        "current_cost_priced": current_cost_priced,
        "gross_invested": gross_invested,
        "realized_pnl": realized_pnl,
        "unrealized": unrealized,
        "cash_income": cash_income,
        "total_pnl": total_pnl,
        "return_pct": return_pct,
        "n_priced": int(len(priced)),
        "n_unpriced": int(len(df) - len(priced)),
    }


def fetch_ibov_return(start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Fetch ^BVSP closes between start and end; return first/last/return %.

    Raises SystemExit on network or empty-frame failure — caller wants a hard
    stop, not a silently-zero comparison.
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise SystemExit(f"yfinance not installed: {e}")

    # yfinance treats `end` as exclusive on daily bars; pad +1 day so the last
    # trading day of the requested range actually shows up.
    try:
        ibov = yf.download(
            "^BVSP",
            start=start.strftime("%Y-%m-%d"),
            end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
        )
    except Exception as e:
        raise SystemExit(f"yfinance fetch failed: {e}")

    if ibov is None or ibov.empty:
        raise SystemExit(
            "yfinance returned no data for ^BVSP — check network / date range."
        )

    # yfinance sometimes returns a MultiIndex on columns when given a single
    # ticker; flatten by taking the 'Close' column at level 0.
    if isinstance(ibov.columns, pd.MultiIndex):
        closes = ibov["Close"].iloc[:, 0]
    else:
        closes = ibov["Close"]
    closes = closes.dropna()
    if closes.empty:
        raise SystemExit("^BVSP closes are all NaN over requested range.")

    first_close = float(closes.iloc[0])
    last_close = float(closes.iloc[-1])
    first_date = pd.Timestamp(closes.index[0]).normalize()
    last_date = pd.Timestamp(closes.index[-1]).normalize()
    return_pct = (last_close - first_close) / first_close * 100.0
    return {
        "first_close": first_close,
        "last_close": last_close,
        "first_date": first_date,
        "last_date": last_date,
        "return_pct": return_pct,
    }


def _earliest_trade_date() -> pd.Timestamp | None:
    if not TRADES.exists():
        return None
    trades = pd.read_csv(TRADES, parse_dates=["date"])
    if trades.empty:
        return None
    return pd.Timestamp(trades["date"].min()).normalize()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--start",
        type=str,
        default=None,
        help="Override start date (YYYY-MM-DD). Default: earliest trade in data/trades.csv.",
    )
    ap.add_argument(
        "--no-annualize",
        dest="annualize",
        action="store_false",
        help="Skip annualized (CAGR) numbers (shown by default).",
    )
    args = ap.parse_args()

    # 1. Determine start.
    if args.start:
        try:
            start = pd.Timestamp(args.start).normalize()
        except ValueError:
            print(f"Bad --start date: {args.start!r}", file=sys.stderr)
            return 1
    else:
        start = _earliest_trade_date()
        if start is None:
            print("No trades in data/trades.csv — nothing to compare.", file=sys.stderr)
            return 0

    # 2. Determine end. Use the latest data date we actually have, capped at
    # today (no point asking yfinance for tomorrow).
    today = pd.Timestamp.today().normalize()
    data_end = _latest_data_date()
    end = min(data_end, today)
    if end <= start:
        print(
            f"End ({end.date()}) is not after start ({start.date()}); cannot compare.",
            file=sys.stderr,
        )
        return 1

    # 3. Portfolio totals. We use snapshot-priced market value as "now" — same
    # convention as portfolio_status. This makes the comparison anchor on the
    # snapshot date, not literally today.
    pf = compute_portfolio_return(end)

    # 4. IBOV.
    ibov = fetch_ibov_return(start, end)

    # 5. Period and annualized.
    days = (end - start).days
    years = days / 365.25
    pf_ann = _annualize(pf["return_pct"], years) if args.annualize else None
    ibov_ann = _annualize(ibov["return_pct"], years) if args.annualize else None

    # 6. Output.
    print(f"Period: {start.date()} -> {end.date()}  (~{years:.2f} years, {days} days)")
    print(f"IBOV bars: {ibov['first_date'].date()} -> {ibov['last_date'].date()}  "
          f"(close {ibov['first_close']:,.0f} -> {ibov['last_close']:,.0f})")
    print()

    rows = [
        ("Return %",
         f"{pf['return_pct']:+.2f}%",
         f"{ibov['return_pct']:+.2f}%"),
    ]
    if args.annualize:
        pf_str = "n/a" if pd.isna(pf_ann) else f"{pf_ann:+.2f}%/yr"
        iv_str = "n/a" if pd.isna(ibov_ann) else f"{ibov_ann:+.2f}%/yr"
        rows.append(("Annualized %", pf_str, iv_str))

    print(tabulate(rows, headers=["Metric", "Portfolio", "IBOV (^BVSP)"],
                   tablefmt="github"))
    print()

    # 7. Alpha.
    alpha = pf["return_pct"] - ibov["return_pct"]
    if alpha >= 0:
        print(f"Outperformed IBOV by {alpha:+.2f} percentage points.")
    else:
        print(f"Underperformed IBOV by {alpha:+.2f} percentage points.")
    if args.annualize and not pd.isna(pf_ann) and not pd.isna(ibov_ann):
        ann_alpha = pf_ann - ibov_ann
        print(f"Annualized alpha: {ann_alpha:+.2f} pp/yr.")
    print()

    # 8. Honest caveats.
    print("Caveats:")
    print("- IBOV (^BVSP) is price-only; it does NOT include dividends. Your")
    print("  portfolio return DOES include dividends, JCP, and FII rendimentos.")
    print("  Brazilian dividend yield runs ~5%/yr, so over multi-year periods")
    print("  this comparison is favorable to your portfolio by roughly 10-12 pp")
    print("  over 3 years just from the dividend differential. The IBOV total-")
    print("  return index (IBXL/IBrX-100 are not the same; there is no public")
    print("  total-return IBOV easily fetchable via yfinance) would be the")
    print("  apples-to-apples benchmark.")
    print("- Portfolio return is approximately money-weighted: capital was")
    print("  deployed at varying times over the period, so early dollars have")
    print("  had longer to compound than recent ones. IBOV return is the raw")
    print("  index price change. These are not perfectly comparable; a")
    print("  proper time-weighted return (TWR) would require daily NAV.")
    print(f"- Portfolio totals use snapshot prices as of {end.date()} for")
    print(f"  {pf['n_priced']} priced positions"
          + (f" ({pf['n_unpriced']} unpriced positions excluded)."
             if pf["n_unpriced"] else "."))
    print("- 'Gross invested' is the sum of all historical buy values, never")
    print("  reduced by sells — so trimmed positions don't artificially inflate")
    print("  the return %. If your B3 Negociacao report didn't cover the full")
    print("  buy history, gross_invested is understated and return % is")
    print("  overstated; check 'incomplete_history' from load_b3.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
