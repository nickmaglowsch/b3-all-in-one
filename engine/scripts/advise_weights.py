"""Advise-only target weights (and an optional rebalance trade list) for a strategy.

No simulation, no history replay: build the market plane up to *today*, run the
strategy's generate_signals + the global dedup, and report the last *live*
target-weight row (all-zero warmup/park rows are skipped). This is the front half
of a backtest with the sim lopped off — the "what should I hold right now" query
the portfolio loop (spec 05, Feature 1) needs.

Usage:
    python scripts/advise_weights.py <strategy_name> [--params '<json>'] \
        [--positions '<json>'] [--nav-cash <brl>]

  --params     strategy parameter overrides, same JSON as run_backtest_json.
  --positions  current book, {"TICKER": {"qty": N, "avg_cost": BRL}, ...}. When
               given (with --nav-cash), also emit an integer-share trade list to
               move the book to the advised weights (pure computation, NO tax
               logic — that lives platform-side).
  --nav-cash   uninvested BRL cash; part of NAV = Σ qty×price + cash.

Prints a one-line JSON object to stdout:
    {strategy, as_of, weights: {ticker: w}, skipped_rows_note[, trades]}

Reads the market plane from Postgres via ENGINE_DATABASE_URL (falls back to the
local sqlite DB when unset). Network mirrors run_backtest_json: IBOV is always
stubbed; set ENGINE_BENCH_OFFLINE=1 to stub CDI too and run fully offline (valid
for price-only strategies, used by the tests).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the exact offline-stub machinery from the JSON backtest runner so the two
# subprocess entry points behave identically (no second copy to drift).
from scripts.run_backtest_json import _install_benchmark_stubs  # noqa: E402


def _latest_prices(raw_close: pd.DataFrame, tickers: set[str]) -> dict[str, float]:
    """Last non-NaN raw (unadjusted) close per requested ticker; drops unpriced
    names (special sleeves like CDI_ASSET have no share price)."""
    if raw_close is None or raw_close.empty:
        return {}
    last = raw_close.ffill().iloc[-1]
    out: dict[str, float] = {}
    for t in tickers:
        if t in last.index:
            v = last[t]
            if pd.notna(v) and float(v) > 0:
                out[t] = float(v)
    return out


def _build_trades(
    positions_in: dict, cash: float, target_row: pd.Series, raw_close: pd.DataFrame
) -> dict:
    """Integer-share trade list to move `positions_in` (+cash) to target_row.

    NAV = Σ qty×price + cash. compute_trades gives BRL diffs; convert to whole
    shares: floor for buys (don't overspend), ceil-capped-at-held for sells
    (round up to fully raise the cash, but never sell more than you hold).
    """
    from backtests.core.simulation import compute_trades

    pos_tickers = set(positions_in)
    tgt_tickers = set(target_row[target_row != 0].index)
    prices = _latest_prices(raw_close, pos_tickers | tgt_tickers)

    eng_pos: dict[str, dict] = {}
    held_qty: dict[str, float] = {}
    for t, p in positions_in.items():
        qty = float(p["qty"])
        avg = float(p["avg_cost"])
        # ponytail: unpriced holding valued at avg_cost so NAV isn't understated;
        # such a ticker still can't be traded below (no est_price) and is listed
        # in unpriced_skipped.
        price = prices.get(t, avg)
        eng_pos[t] = {"current_value": qty * price, "cost_basis": qty * avg}
        held_qty[t] = qty

    nav = sum(v["current_value"] for v in eng_pos.values()) + cash
    sells_brl, buys_brl = compute_trades(eng_pos, target_row, nav)

    sells, buys, skipped = [], [], []
    for t, brl in sells_brl.items():
        price = prices.get(t)
        if price is None:
            skipped.append(t)
            continue
        qty = min(int(held_qty.get(t, 0.0)), math.ceil(brl / price))
        if qty <= 0:
            continue
        sells.append({"ticker": t, "qty": qty, "est_price": price, "est_brl": qty * price})
    for t, brl in buys_brl.items():
        price = prices.get(t)
        if price is None:
            skipped.append(t)
            continue
        qty = math.floor(brl / price)
        if qty == 0:
            continue
        buys.append({"ticker": t, "qty": qty, "est_price": price, "est_brl": qty * price})

    return {
        "nav": nav,
        "sells": sells,
        "buys": buys,
        "unpriced_skipped": sorted(set(skipped)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--params", default="", help="JSON param overrides")
    ap.add_argument("--positions", default="", help='JSON {ticker:{qty,avg_cost}}')
    ap.add_argument("--nav-cash", type=float, default=0.0, help="uninvested BRL")
    args = ap.parse_args()

    from backtests.core.strategy_registry import get_registry
    from backtests.core.strategy_base import dedup_target_weights

    strat = get_registry().get(args.strategy)
    params = strat.get_default_parameters()
    params.update(json.loads(args.params) if args.params else {})

    start = str(params.get("start_date", "2005-01-01"))
    end = datetime.today().strftime("%Y-%m-%d")
    freq = str(params.get("rebalance_freq", "ME"))
    needs_funds = bool(getattr(strat, "needs_fundamentals", False))

    offline = os.environ.get("ENGINE_BENCH_OFFLINE") == "1"
    _install_benchmark_stubs(start, end, offline)

    from backtests.core.shared_data import build_shared_data

    db_path = os.environ.get("ENGINE_DATABASE_URL") or str(ROOT / "b3_market_data.sqlite")
    shared = build_shared_data(db_path, start, end, freq, include_fundamentals=needs_funds)

    _, tw = strat.generate_signals(shared, params)
    tw = dedup_target_weights(tw, shared["adtv"])

    # Last live row: skip all-zero warmup rows (before inception) and park/hold
    # rows (emitted mid-series when a strategy has no valid targets).
    sums = tw.fillna(0.0).abs().sum(axis=1)
    live = sums > 1e-9
    if not bool(live.any()):
        raise SystemExit(
            f"strategy '{args.strategy}' produced no live target rows for {start}..{end}"
        )
    as_of = live[live].index[-1]
    trailing_skipped = len(tw) - 1 - tw.index.get_indexer([as_of])[0]

    row = tw.loc[as_of].fillna(0.0)
    weights = {str(t): float(w) for t, w in row.items() if w != 0}

    out = {
        "strategy": args.strategy,
        "as_of": as_of.date().isoformat(),
        "weights": weights,
        "skipped_rows_note": (
            f"last live row {as_of.date().isoformat()}; "
            f"{int(trailing_skipped)} trailing all-zero (warmup/park) row(s) skipped"
        ),
    }

    if args.positions:
        positions_in = json.loads(args.positions)
        out["trades"] = _build_trades(positions_in, args.nav_cash, row, shared["raw_close"])

    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
