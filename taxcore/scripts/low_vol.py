"""Ibovespa Smart Low Volatility B3 replica — current portfolio + backtest.

Reproduces B3's low-vol index (see methodologies/low_vol.py): from the most
liquid B3 names (Ibov proxy), pick the 33% with the lowest 252-day EWMA
volatility, one asset per company, inverse-vol weighted, capped at 10%.

Two outputs:
  1. The target portfolio as of the latest data (weights table).
  2. A total-return backtest: rebalanced every 4 months (Ibov cadence),
     drift within each period, charted vs IBOV and CDI (base 100).

Universe is the top-N liquid tickers from the latest equity snapshot — a
static, survivorship-biased proxy (disclosed). Prices/benchmarks from Yahoo,
CDI from BCB SGS, reusing scripts.portfolio_index helpers.

Usage:
    python -m scripts.low_vol                      # full history backtest
    python -m scripts.low_vol --universe 90 --start 2018-01-01
    python -m scripts.low_vol --no-backtest        # just today's weights
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

from methodologies.low_vol import ewma_vol, select_portfolio
from scripts._common import annualize, latest_csv, SNAPSHOTS
from scripts.portfolio_index import (
    C_IBOV, C_CDI, C_PORTFOLIO, REPORTS, fetch_cdi, fetch_yahoo, plot)

warnings.filterwarnings("ignore")

# Ibov rebalances 3x/year (first business day of Jan, May, Sep). The low-vol
# index rebalances with it, so we rebalance every 4 months.
REBAL_MONTHS = (1, 5, 9)


def universe(n: int) -> pd.Series:
    """Top-n tickers by liq2m from the latest equity snapshot -> liq2m map."""
    snap = latest_csv(SNAPSHOTS)
    if snap is None:
        raise SystemExit("No equity snapshot — run scripts/fetch_fundamentus.py")
    liq = (snap.set_index("papel")["liq2m"].dropna()
           .sort_values(ascending=False).head(n))
    return liq


def rebalance_dates(grid: pd.DatetimeIndex, warmup: int) -> list[pd.Timestamp]:
    """First grid day of each REBAL_MONTHS month, after `warmup` days of data."""
    start = grid[warmup]
    marks = [d for d in grid[warmup:]
             if d.month in REBAL_MONTHS and d.day <= 7]
    # One per (year, month).
    seen, out = set(), []
    for d in marks:
        key = (d.year, d.month)
        if key not in seen and d >= start:
            seen.add(key)
            out.append(d)
    return out


def backtest(prices: pd.DataFrame, liq: pd.Series, grid: pd.DatetimeIndex,
             pct: float, cap: float, n: int = 252) -> pd.Series:
    """Chain a total-return index (base 100) with 4-monthly rebalances.

    At each rebalance we compute EWMA vol from data up to that day, form target
    weights, and hold (letting weights drift with price) until the next one.
    """
    vol = ewma_vol(prices, n)
    rets = prices.pct_change().fillna(0.0)
    dates = rebalance_dates(grid, warmup=n)
    if not dates:
        raise SystemExit("Not enough history for one 252-day warmup + rebalance.")

    nav = pd.Series(index=grid, dtype=float)
    val = 100.0
    holdings = pd.Series(dtype=float)  # per-ticker R$ value
    bounds = dates + [grid[-1] + pd.Timedelta(days=1)]
    for i, rb in enumerate(dates):
        w = select_portfolio(vol.loc[rb], liq, pct, cap)
        holdings = val * w  # reset to target
        period = grid[(grid >= rb) & (grid < bounds[i + 1])]
        for d in period:
            holdings = holdings * (1.0 + rets.loc[d, holdings.index])
            val = float(holdings.sum())
            nav[d] = val
    return nav.dropna()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", type=int, default=90,
                    help="Ibov proxy size: top-N by liquidity (default 90).")
    ap.add_argument("--pct", type=float, default=0.33,
                    help="Fraction of the universe to hold (default 0.33).")
    ap.add_argument("--cap", type=float, default=0.10,
                    help="Max single-name weight (default 0.10).")
    ap.add_argument("--start", default="2016-01-01",
                    help="Earliest date to fetch (needs +1yr warmup).")
    ap.add_argument("--no-backtest", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    liq = universe(args.universe)
    tickers = list(liq.index)
    print(f"Universe: top {len(tickers)} by liq2m (Ibov proxy).")

    start = pd.Timestamp(args.start)
    symbols = [f"{t}.SA" for t in tickers] + ["^BVSP"]
    print(f"Fetching {len(symbols)} symbols from Yahoo since {start.date()}...")
    data = fetch_yahoo(symbols, start)

    have = [t for t in tickers if f"{t}.SA" in data]
    missing = [t for t in tickers if t not in have]
    grid = pd.bdate_range(start, pd.Timestamp.today().normalize())
    prices = pd.DataFrame(
        {t: data[f"{t}.SA"]["Close"].reindex(grid) for t in have})
    # Yahoo closes are split-adjusted to today, consistent for return calc.
    liq = liq.reindex(have)

    # ---- Current target portfolio ----
    vol_now = ewma_vol(prices).iloc[-1]
    w = select_portfolio(vol_now, liq, args.pct, args.cap)
    from tabulate import tabulate
    rows = [(t, f"{vol_now[t] * 100:5.1f}%", f"{w[t] * 100:5.2f}%")
            for t in w.sort_values(ascending=False).index]
    print(f"\nTarget portfolio ({len(w)} names, as of {grid[-1].date()}):")
    print(tabulate(rows, headers=["Ticker", "EWMA vol (ann.)", "Weight"],
                   tablefmt="github"))
    if missing:
        print(f"\nNo Yahoo history (dropped from universe): {missing}")

    if args.no_backtest:
        return 0

    # ---- Backtest ----
    print("\nBacktesting (4-monthly rebalance)...")
    idx = backtest(prices, liq, grid, args.pct, args.cap)
    bt_grid = idx.index
    ibov = data["^BVSP"]["Close"].reindex(bt_grid).ffill()
    cdi = fetch_cdi(bt_grid[0], bt_grid[-1]).reindex(bt_grid).ffill().bfill()

    series = {"Ibov Low Vol (replica)": (idx, C_PORTFOLIO),
              "IBOV": (ibov, C_IBOV), "CDI": (cdi, C_CDI)}
    series = {k: (s.dropna() / s.dropna().iloc[0] * 100.0, c)
              for k, (s, c) in series.items()}

    first, last = bt_grid[0], bt_grid[-1]
    years = (last - first).days / 365.25
    rows = []
    for name, (s, _) in series.items():
        tot = s.iloc[-1] - 100.0
        vol_ann = s.pct_change().std() * (252 ** 0.5) * 100
        rows.append((name, f"{s.iloc[-1]:,.1f}", f"{tot:+.1f}%",
                     f"{annualize(tot, years):+.2f}%/yr", f"{vol_ann:.1f}%"))
    print(f"\nBacktest: {first.date()} -> {last.date()}  (~{years:.2f} yr)")
    print(tabulate(rows, headers=["Series", "Index", "Total", "CAGR",
                                  "Realized vol"], tablefmt="github"))

    out = Path(args.out) if args.out else REPORTS / f"{last.date()}-low-vol-backtest.png"
    plot(series, out, subtitle=(
        f"{first.date()} -> {last.date()} · top-{args.universe} liquidity "
        f"universe (Ibov proxy) · lowest {args.pct:.0%} EWMA-252 vol · "
        f"inverse-vol, {args.cap:.0%} cap · 4-monthly rebalance · CDI gross"),
        title="Ibovespa Smart Low Volatility B3 — replica backtest, base 100")
    print(f"\nChart saved -> {out}")

    print("\nCaveats:")
    print("- Universe = top-N by liq2m from the latest snapshot (Ibov proxy).")
    print("  The snapshot retains delisted rows, so a name that died with stale")
    print("  liquidity is still in-universe; only names now at liq2m=0 drop out.")
    print("  Fully point-in-time membership would need per-rebalance snapshots.")
    print("- EWMA follows the standard span-252 convention; the B3 annex's")
    print("  literal a/(1-a) placement is read as transposed (see module docs).")
    print("- Gross of taxes/costs on all series; rebalancing turnover not costed.")
    print("- One-asset-per-company dedup uses liquidity as a free-float proxy.")
    print("- This is analysis, not a recommendation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
