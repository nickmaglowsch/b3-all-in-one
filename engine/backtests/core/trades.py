"""The rebalance diff: BRL to sell and buy per ticker to reach target weights.

Pure, so both the simulator (simulation._execute_rebalance) and the live
advisor (scripts/advise_weights.py) size trades the same way. Split out of
simulation.py; it re-exports compute_trades for the callers that import it there.
"""
from __future__ import annotations

import pandas as pd


def compute_trades(
    positions: dict,
    target_weights: pd.Series,
    nav: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Pure rebalance diff: BRL to sell and buy per ticker to hit target weights.

    Args:
        positions: current ledger, {ticker: {"cost_basis", "current_value"}}.
        target_weights: target weight per ticker for this rebalance row.
        nav: portfolio value to rebalance to (may include a pending buy-in).

    Returns:
        (sells, buys) in BRL.
          sells[t] = cash to raise by selling a long OR covering a short — the
                     amount that must go through the tax engine.
          buys[t]  = cash to deploy into a long, OR (negative) capital to open /
                     extend a short (never taxed: opening a position realizes no
                     gain).

    Routing follows the *current* sign of the holding, not whether it is held:
      • diff < 0 while flat or short opens / deepens a short: a negative *buy*,
        never taxed. (As a sell it read as a full cover and dropped the short.)
      • diff > 0 while short is a *sell*: covering realizes taxable P&L.
      • crossing zero is close-then-open: the whole position through sells,
        the new side through buys.
    Pure: no mutation of `positions`. The order is not irrelevant, though: the
    caller sums over these dicts and opens positions in their order, and float
    addition is not associative. A set here made a run's last bits depend on the
    process's string hash seed — held book first, then new targets, in order.
    """
    sells: dict[str, float] = {}  # {ticker: cash_to_raise}
    buys: dict[str, float] = {}   # {ticker: cash_to_deploy}

    all_tickers = dict.fromkeys([*positions, *target_weights[target_weights != 0].index])
    for t in all_tickers:
        current_val = positions.get(t, {}).get("current_value", 0.0)
        target_w = target_weights.get(t, 0.0)
        if pd.isna(target_w):
            target_w = 0.0

        target_val = nav * target_w
        diff = target_val - current_val

        if diff < -0.01:            # less exposure: sell a long, or open/deepen a short
            if current_val > 0:
                sells[t] = current_val if target_val < 0 else abs(diff)
                if target_val < 0:
                    buys[t] = target_val    # flip: close the long, open the short
            else:
                buys[t] = diff              # negative capital, never taxed
        elif diff > 0.01:           # more exposure: buy a long, or cover a short
            if current_val < 0:
                sells[t] = abs(current_val) if target_val > 0 else diff
                if target_val > 0:
                    buys[t] = target_val    # flip: cover the short, open the long
            else:
                buys[t] = diff

    return sells, buys
