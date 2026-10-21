"""Option activity in a Negociação extract: premiums and exercises.

Negociação books an option exercise under the option-series code and a premium
as if it were a trade on that code. Neither is a position in the underlying the
way load_b3 counts one, so `process_option_activity` takes them out of the
trade list: premiums become cash_events, exercises become trades on the
underlying they settled in. Split from load_b3.py, which is past the size the
quality gate lets a module grow to.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _resolve_exercise_underlying(
    exercises: pd.DataFrame,
    mov_settlements: pd.DataFrame,
    window_days: int = 5,
) -> dict:
    """Match each Negociação exercise row to its Movimentação settlement row.

    Negociação records option exercises under the option-series code
    (e.g. 'B3SAW130E'); the underlying ticker only appears in the matching
    Movimentação 'Transferência - Liquidação' row. read_movimentacao has
    already walked that row back from settlement to trade date, so the two
    usually agree to the day; the window, either side, absorbs a holiday the
    calendar does not know. We join by qty+price, nearest date first, and
    return {exercise_idx: underlying}.
    """
    if exercises.empty or mov_settlements.empty:
        return {}

    s = mov_settlements.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.dropna(subset=["date", "ticker", "qty", "price"])

    resolved: dict = {}
    used: set = set()
    for ex_idx, ex in exercises.iterrows():
        ex_date = pd.to_datetime(ex["date"])
        ex_qty = float(ex["qty"])
        ex_price = float(ex["price"])
        gap = (s["date"] - ex_date).dt.days.abs()
        mask = (
            (~s.index.isin(used))
            & (gap <= window_days)
            & ((s["qty"] - ex_qty).abs() < 1e-3)
            & ((s["price"] - ex_price).abs() < 1e-4)
        )
        candidates = s[mask].assign(__gap=gap[mask])
        if candidates.empty:
            continue
        best = candidates.sort_values(["__gap", "date"]).iloc[0]
        resolved[ex_idx] = best["ticker"]
        used.add(best.name)
    return resolved


EMPTY_OPT_STATS = {
    "exercises_resolved": 0,
    "exercises_unresolved": 0,
    "option_trades_processed": 0,
    "net_premium_brl": 0.0,
}


def process_option_activity(
    neg_trades: pd.DataFrame,
    mov_settlements: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list, dict]:
    """Split Negociação trades into equity-only trades + premium cash events.

    Three input row classes are handled:
      • Mercado contains 'Opção' AND not 'Exercício'  → option premium trades.
        Each becomes a cash_events row with event_category='premium', ticker
        set to the 4-letter underlying root. Sells (+cash), buys (-cash).
      • Mercado starts with 'Exercício'  → assignment/exercise of an option.
        The row is rewritten as a trade on the resolved underlying ticker
        (Mercado à Vista), so it flows into derive_positions like a normal buy
        or sell. Unresolved exercises (no Movimentação match found) are
        dropped and reported back to the caller.
      • everything else → returned unchanged.

    Returns (clean_trades, premium_events, unresolved_details, stats).
    """
    if neg_trades.empty:
        return neg_trades, pd.DataFrame(), [], dict(EMPTY_OPT_STATS)

    market = neg_trades["market"].fillna("").astype(str)
    is_exercise = market.str.startswith("Exercício")
    is_premium = market.str.contains("Opção") & ~is_exercise

    non_opt = neg_trades[~(is_exercise | is_premium)].copy()
    exercises = neg_trades[is_exercise].copy()
    premiums = neg_trades[is_premium].copy()

    # Resolve each exercise's underlying ticker via the Mov settlement row.
    resolved = _resolve_exercise_underlying(exercises, mov_settlements)
    rewritten_rows = []
    unresolved_details: list = []
    for ex_idx, ex in exercises.iterrows():
        underlying = resolved.get(ex_idx)
        if underlying is None:
            unresolved_details.append({
                "date": str(pd.to_datetime(ex["date"]).date()),
                "code": ex["ticker"],
                "side": ex["side"],
                "qty": float(ex["qty"]),
                "price": float(ex["price"]),
                "market": ex["market"],
            })
            continue
        new = ex.copy()
        new["ticker"] = underlying
        new["market"] = "Mercado à Vista"
        new["maturity"] = "-"
        rewritten_rows.append(new)
    rewritten = (
        pd.DataFrame(rewritten_rows) if rewritten_rows else exercises.iloc[0:0]
    )
    clean_trades = (
        pd.concat([non_opt, rewritten], ignore_index=True)
        if not rewritten.empty else non_opt
    )

    # Build premium cash_events: one row per option open/close trade.
    # Ticker is the option-series code (e.g. 'CMIGI18'), not the underlying
    # root: distinct contracts on the same underlying often share date+qty+
    # amount and would collide under cash_events' dedup key. The series code
    # is unique per contract so true duplicates still collapse and real
    # distinct trades survive. Grep by 'startswith(\"CMIG\")' to roll up.
    premium_events = pd.DataFrame()
    if not premiums.empty:
        signed_amount = premiums["amount"].astype(float) * np.where(
            premiums["side"].astype(str).str.lower().str.startswith("venda"), 1.0, -1.0)
        premium_events = pd.DataFrame({
            "date":            premiums["date"].values,
            "ticker":          premiums["ticker"].astype(str).values,
            "event_type":      premiums["market"].values,
            "event_category":  "premium",
            "is_amortization": False,
            "qty":             premiums.get("signed_qty",
                                            premiums["qty"]).values,
            "price":           premiums["price"].values,
            "amount":          signed_amount.values,
            "broker":          premiums["broker"].values
                               if "broker" in premiums else "",
            "__source_file":   premiums["__source_file"].values
                               if "__source_file" in premiums else "",
        })

    stats = {
        "exercises_resolved": len(resolved),
        "exercises_unresolved": len(unresolved_details),
        "option_trades_processed": int(len(premiums)),
        "net_premium_brl": round(
            float(premium_events["amount"].sum())
            if not premium_events.empty else 0.0, 2),
    }
    return clean_trades, premium_events, unresolved_details, stats
