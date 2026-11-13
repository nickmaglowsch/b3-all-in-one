"""Advise-only target weights (and an optional rebalance trade list) for a strategy.

No simulation, no history replay: build the market plane up to *today*, run the
strategy's generate_signals + the global dedup, and report the last *live*
target-weight row (all-zero warmup/park rows are skipped). This is the front half
of a backtest with the sim lopped off — the "what should I hold right now" query
the portfolio loop (spec 05, Feature 1) needs.

Usage:
    python scripts/advise_weights.py <strategy_name> [--params '<json>'] \
        [--positions '<json>'] [--nav-cash <brl>]
    python scripts/advise_weights.py --weights '<json>' [--markets '<json>'] \
        [--positions '<json>'] [--nav-cash <brl>]

  --params     strategy parameter overrides, same JSON as run_backtest_json.
  --weights    fixed target weights, {"TICKER": w, ...}, as an alternative to a
               strategy name (the positional is then omitted). Signal generation
               is skipped entirely: the tickers are priced from their latest
               close in BRL (scripts/weights_pricing.py) and, with
               --positions/--nav-cash, the same trade list is produced. "CASH_CDI"
               is treated as the cash sleeve — it is excluded from the trades and
               its weight simply stays uninvested.
  --markets    with --weights: {"TICKER": "US" | "CRYPTO"} for the tickers that
               are not B3 codes, priced from their own plane in reais. A crypto
               quantity is fractional (8 decimals); every other is whole units.
               Keyed by symbol alone, as --weights and --positions are: the
               platform refuses one symbol as a sleeve on two markets
               (portfolio_sleeves.one_market_per_symbol), so a key is one asset.
  --positions  current book, {"TICKER": {"qty": N, "avg_cost": BRL}, ...}. When
               given (with --nav-cash), also emit a whole-unit trade list to
               move the book to the advised weights (pure computation, NO tax
               logic — that lives platform-side).
  --nav-cash   uninvested BRL cash; part of NAV = Σ qty×price + cash.

Prints a one-line JSON object to stdout:
    {strategy, as_of, weights: {ticker: w}, skipped_rows_note[, trades]}
(strategy is null in --weights mode.)

Reads the market plane from Postgres via ENGINE_DATABASE_URL (falls back to the
local sqlite DB when unset). Network mirrors run_backtest_json: IBOV comes off
the plane; set ENGINE_BENCH_OFFLINE=1 to stub it and CDI and run fully offline
(valid for price-only strategies, used by the tests).
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

from scripts import weights_pricing  # noqa: E402

# Distinct exit code the platform (platform_app/enginerun.py) keys on: the
# strategy needs CVM fundamentals but fundamentals_pit is empty in the active
# engine DB. Any other failure keeps the ordinary non-zero/stringly exit.
NEEDS_FUNDAMENTALS_EXIT = 3


def _fundamentals_empty(db_path: str | None) -> bool:
    """True when fundamentals_pit has 0 rows in the active engine DB.

    Resolves the backend exactly as the strategies do (storage.get_connection:
    ENGINE_DATABASE_URL wins, else the strategy's db_path). Any DB/table error →
    treat as *not* provably empty, so we fall back to the generic no-rows error
    rather than misattribute an unrelated failure to missing fundamentals.
    """
    from b3_pipeline import storage

    try:
        with storage.get_connection(db_path) as conn:
            n = storage.read_sql("SELECT COUNT(*) AS n FROM fundamentals_pit", conn)
        return int(n.iloc[0]["n"]) == 0
    except Exception:
        return False


def _exit_needs_fundamentals(strategy: str) -> None:
    """Emit the machine-detectable 'needs fundamentals' error (JSON on stderr)
    and exit with NEEDS_FUNDAMENTALS_EXIT."""
    err = {
        "error": "needs_fundamentals",
        "strategy": strategy,
        "message": (
            f"strategy '{strategy}' requires fundamentals data, which has not "
            f"been ingested in this environment"
        ),
    }
    json.dump(err, sys.stderr)
    sys.stderr.write("\n")
    raise SystemExit(NEEDS_FUNDAMENTALS_EXIT)


def _latest_prices(raw_close: pd.DataFrame) -> dict[str, float]:
    """Last non-NaN raw (unadjusted) close per ticker; drops unpriced names
    (special sleeves like CDI_ASSET have no share price)."""
    if raw_close is None or raw_close.empty:
        return {}
    last = raw_close.ffill().iloc[-1]
    return {str(t): float(v) for t, v in last.items()
            if pd.notna(v) and float(v) > 0}


# A crypto pair trades in fractions of a coin: a whole-unit floor would turn any
# sleeve smaller than one BTC (~R$ 500k) into no trade at all.
_FRACTION_STEP = 1e-8


def _units(brl: float, price: float, step: float, up: bool) -> float:
    """BRL → units in `step`s: ceil for a sell (fully raise the cash), floor for
    a buy (never overspend). Whole units when step is 1; a fractional count is
    rounded first so float noise (0.3 / 1e-8 = 29999999.99…) costs no step."""
    rnd = math.ceil if up else math.floor
    if step == 1:
        return rnd(brl / price)
    return round(rnd(round(brl / price / step, 6)) * step, 8)


def _build_trades(
    positions_in: dict, cash: float, target_row: pd.Series, all_prices: dict[str, float],
    fractional: frozenset = frozenset(),
) -> dict:
    """Trade list to move `positions_in` (+cash) to target_row.

    NAV = Σ qty×price + cash. compute_trades gives BRL diffs; convert to whole
    shares (fractions of a coin for the `fractional` tickers): floor for buys
    (don't overspend), ceil-capped-at-held for sells (round up to fully raise
    the cash, but never sell more than you hold). A buy into a name not held
    that rounds to nothing is listed in `below_one_lot` rather than dropped
    without a word — a top-up smaller than a share stays the rounding it is.

    `all_prices` is {ticker: latest close} for the whole plane; the two callers
    source it differently (a built shared frame vs. a direct query) and this
    narrows it to the names actually in play.
    """
    from backtests.core.simulation import compute_trades

    pos_tickers = set(positions_in)
    tgt_tickers = set(target_row[target_row != 0].index)
    prices = {t: all_prices[t] for t in (pos_tickers | tgt_tickers) if t in all_prices}

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

    sells, buys, skipped, small = [], [], [], []
    for t, brl in sells_brl.items():
        price = prices.get(t)
        if price is None:
            skipped.append(t)
            continue
        step = _FRACTION_STEP if t in fractional else 1
        held = held_qty.get(t, 0.0) if step != 1 else int(held_qty.get(t, 0.0))
        qty = min(held, _units(brl, price, step, up=True))
        if qty <= 0:
            continue
        sells.append({"ticker": t, "qty": qty, "est_price": price, "est_brl": qty * price})
    for t, brl in buys_brl.items():
        price = prices.get(t)
        if price is None:
            skipped.append(t)
            continue
        qty = _units(brl, price, _FRACTION_STEP if t in fractional else 1, up=False)
        if qty == 0:
            if t not in held_qty:  # a position it cannot open, not a rounding residue
                small.append(t)
            continue
        buys.append({"ticker": t, "qty": qty, "est_price": price, "est_brl": qty * price})

    return {
        "nav": nav,
        "sells": sells,
        "buys": buys,
        "unpriced_skipped": sorted(set(skipped)),
        # New positions worth less than one unit: priced, wanted, not tradable.
        "below_one_lot": sorted(small),
        # Per-ticker current market value (avg_cost fallback for unpriced holdings).
        # Lets the platform derive current weights / NAV from the same valuation the
        # trade math uses, no separate price query.
        "positions_valued": {t: v["current_value"] for t, v in eng_pos.items()},
    }


def _build_shared(start: str, end: str, freq: str, needs_funds: bool):
    """Install the benchmark stubs and build the market plane once."""
    offline = os.environ.get("ENGINE_BENCH_OFFLINE") == "1"
    _install_benchmark_stubs(offline)
    from backtests.core.shared_data import build_shared_data

    db_path = os.environ.get("ENGINE_DATABASE_URL") or str(ROOT / "b3_market_data.sqlite")
    return build_shared_data(db_path, start, end, freq, include_fundamentals=needs_funds)


def _advise_from_strategy(args) -> dict:
    from backtests.core.strategy_registry import get_registry
    from backtests.core.strategy_base import dedup_target_weights
    from backtests.core.data import SectorRegistryEmpty
    from backtests.core.fundamentals import FundamentalsNotLoaded

    strat = get_registry().get(args.strategy)
    params = strat.get_default_parameters()
    params.update(json.loads(args.params) if args.params else {})

    start = str(params.get("start_date", "2005-01-01"))
    end = datetime.today().strftime("%Y-%m-%d")
    freq = str(params.get("rebalance_freq", "ME"))
    needs_funds = bool(getattr(strat, "needs_fundamentals", False))

    shared = _build_shared(start, end, freq, needs_funds)

    try:
        _, tw = strat.generate_signals(shared, params)
    except (SectorRegistryEmpty, FundamentalsNotLoaded):  # a fixable data gap,
        _exit_needs_fundamentals(args.strategy)  # the CVM ingest -> 422, not 500
    tw = dedup_target_weights(tw, shared["adtv"])

    # Last live row: skip all-zero warmup rows (before inception) and park/hold
    # rows (emitted mid-series when a strategy has no valid targets).
    sums = tw.fillna(0.0).abs().sum(axis=1)
    live = sums > 1e-9
    if not bool(live.any()):
        # Distinguish "needs fundamentals that aren't ingested" (a fixable
        # environment gap the platform surfaces as a clean 422) from a genuine
        # empty result. Only when the strategy declares the dependency AND the
        # table is actually empty do we emit the structured error.
        if needs_funds and _fundamentals_empty(params.get("db_path")):
            _exit_needs_fundamentals(args.strategy)
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
        db_path = os.environ.get("ENGINE_DATABASE_URL") or str(ROOT / "b3_market_data.sqlite")
        out["trades"] = _build_trades(
            json.loads(args.positions), args.nav_cash, row,
            {**weights_pricing.rf_etf_closes(db_path, end),
             **_latest_prices(shared["raw_close"])})
    return out


def _advise_from_weights(args) -> dict:
    """Fixed-weight mode: no signals, and — since nothing here reads a return
    series, a rolling window or a benchmark — no shared-data build either. The
    latest close per ticker comes straight out of its plane (weights_pricing):
    the B3 standard lots and RF ETFs first, then any other B3 code and every
    --markets ticker by name. CASH_CDI is the cash sleeve, kept out of the
    tradeable target so its share of NAV just stays BRL."""
    weights_in = json.loads(args.weights)
    off_b3 = json.loads(getattr(args, "markets", "") or "{}")
    start, end = "2005-01-01", datetime.today().strftime("%Y-%m-%d")
    db_path = os.environ.get("ENGINE_DATABASE_URL") or str(ROOT / "b3_market_data.sqlite")

    as_of, prices = weights_pricing.latest_plane_close(db_path, start, end)
    if as_of is None:
        raise SystemExit("no price data available to value --weights")
    prices = {**weights_pricing.rf_etf_closes(db_path, end), **prices}

    row = pd.Series(
        {t: float(w) for t, w in weights_in.items() if t != "CASH_CDI"}, dtype=float)
    prices |= weights_pricing.sleeve_closes(
        db_path, [t for t in row.index if t not in prices], off_b3, end)

    out = {
        "strategy": None,
        "as_of": as_of.date().isoformat(),
        "weights": {str(t): float(w) for t, w in weights_in.items()},
        "skipped_rows_note": "weights mode: signals skipped; priced from latest close",
    }
    if args.positions:
        out["trades"] = _build_trades(
            json.loads(args.positions), args.nav_cash, row, prices,
            fractional=frozenset(t for t, m in off_b3.items() if m == "CRYPTO"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", nargs="?", help="strategy name; omit when using --weights")
    ap.add_argument("--params", default="", help="JSON param overrides")
    ap.add_argument("--weights", default="", help='JSON {ticker: w} fixed target')
    ap.add_argument("--markets", default="", help='JSON {ticker: US|CRYPTO}, with --weights')
    ap.add_argument("--positions", default="", help='JSON {ticker:{qty,avg_cost}}')
    ap.add_argument("--nav-cash", type=float, default=0.0, help="uninvested BRL")
    args = ap.parse_args()

    # Same ceiling the backtest runner takes, for the same reason: this builds
    # the whole market plane too, and the API spawns one of these per portfolio
    # sleeve. A MemoryError names a line; a SIGKILL names nothing.
    from backtests.core import memory
    memory.guard()

    if bool(args.strategy) == bool(args.weights):
        ap.error("pass exactly one of <strategy> or --weights")

    out = _advise_from_weights(args) if args.weights else _advise_from_strategy(args)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
