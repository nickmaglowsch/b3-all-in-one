"""Signal warm-up, taken from before the window instead of from inside it.

A ranked strategy cannot rank on twelve months of momentum until it has twelve
months of returns. The runner used to load the market from the requested start,
so that history came out of the window itself: MomSharpe asked for 2019 sat in
cash through 2019 and into 2020 and never saw the COVID crash, and a split-window
test compared books that were not invested over the same dates — a 2019 start
of the 40/40/20 blend printed a -10% max drawdown against -25% for the same
blend over 2013..2026.

So the runner now loads `months_for(...)` of extra history before `start`, lets
the strategy build its signals over all of it, and cuts the pre-roll off with
`in_window` before anything is simulated. The pre-roll feeds signals and nothing
else: no trade, tax event or NAV point lands before `start`. Look-ahead is not
touched — a weight at row t still reads row t-1 — the rows before the window
are only *there* now.

When the plane does not reach back that far (a window opening at the first bar
of the archive) the warm-up falls back inside the window, as before, and
`late_books` says so in `data_warnings`.
"""
from __future__ import annotations

import ast
import math
import operator
import os

import pandas as pd

from backtests.core.config_strategy import FixedWeight, RankAndHold, _signal_defs

# A hand-written strategy keeps its warm-up in a loop this module cannot read
# (`range(lookback + 2, ...)`, a three-year fundamentals window, a 400-day SMA),
# so it gets one generous pre-roll that covers every one of them shipped today.
CODE_MONTHS = 36
# Slack on top of what a spec declares: a period label is the period's *end*, so
# a pre-roll cut at an arbitrary date loses up to one period off its front.
MARGIN_MONTHS = 3
# The ceiling. A longer load is a bigger heap (see backtests.core.memory), and a
# window that needs more than ten years of warm-up is a spec to look at, not to
# feed.
MAX_MONTHS = 120

_MONTHS_PER_PERIOD = {"ME": 1.0, "QE": 3.0, "W-FRI": 7 / 30.44}
# Windows inside `at_rebalance(...)` count sessions, not grid periods. 21 is a B3
# month; a 30-session crypto month makes this an overestimate, the safe side.
_DAILY_MONTHS = 1 / 21

# What the bundle derives for every spec before its own expression runs: the
# glitch mask is a 12-period rolling max, mf_composite a skip-1 12-period
# momentum, the regime flags a 10-period mean and a percentile rank that starts
# at 12 periods, `ma200_m` 200 sessions. Covered by 13 periods and a year.
_BUNDLE_PERIODS = 13
_BUNDLE_MONTHS = 12.0

# DSL functions whose second argument is a look-back, and what it defaults to.
_WINDOWED = {"shift", "roll_sum", "roll_mean", "roll_std", "roll_var", "roll_min",
             "roll_max", "roll_median", "roll_skew", "roll_kurt", "pct_change",
             "diff", "ewm_mean", "ewm_std"}
_DEFAULT_WINDOW = {"pct_change": 1.0, "diff": 1.0}
_ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv}

WARNING = "warmup_inside_window"


def months_for(strat, params: dict, freq: str) -> int:
    """Months of history to load before `start` so `strat` is live on it.

    ``ENGINE_WARMUP_MONTHS`` overrides it for every strategy; 0 is the old
    behaviour, warm-up inside the window — the knob to reach for if a short
    window's pre-roll is what tips a run over its memory budget.
    """
    override = os.environ.get("ENGINE_WARMUP_MONTHS", "").strip()
    if override:
        return max(0, int(override))
    need = _strategy_months(strat, params, _MONTHS_PER_PERIOD.get(freq, 1.0))
    return int(min(MAX_MONTHS, math.ceil(need) + MARGIN_MONTHS))


def load_start(start: str, months: int) -> str:
    """The date the plane is read from: `months` before `start`."""
    return (pd.Timestamp(start) - pd.DateOffset(months=months)).strftime("%Y-%m-%d")


def in_window(frame, start):
    """`frame`'s rows on or after `start` — the pre-roll cut off.

    A grid label is the end of its period, so the first label kept is the period
    `start` falls in: the same first row a run loaded from `start` had.
    """
    return frame.loc[frame.index >= pd.Timestamp(start)]


def _strategy_months(strat, params: dict, mpp: float) -> float:
    if isinstance(strat, FixedWeight):
        return _blend_months(strat.spec, params, mpp)
    if isinstance(strat, RankAndHold):
        return _ranker_months(strat.spec, params, mpp)
    return float(CODE_MONTHS)


def _blend_months(spec: dict, params: dict, mpp: float) -> float:
    """The longest warm-up among a blend's strategy sleeves.

    A nested sleeve runs on the blend's grid and with the blend's params over
    its own defaults (FixedWeight.generate_signals), so it is sized the same way.
    One period at least: a ticker sleeve's first return needs the mark before it.
    """
    from backtests.core.strategy_registry import get_registry

    need = mpp
    for sleeve in spec.get("sleeves", []):
        if "strategy" in sleeve:
            sub = get_registry().get(sleeve["strategy"])
            sub_params = {**sub.get_default_parameters(), **params}
            need = max(need, _strategy_months(sub, sub_params, mpp))
    return need


def _ranker_months(spec: dict, params: dict, mpp: float) -> float:
    """A rank_and_hold spec's warm-up: what its own loop skips (`warmup`, or
    lookback + warmup_pad), what its regime flags read, what the windows in its
    expression add up to, and what the bundle needs — whichever is longest."""
    sig = spec.get("signal", {})
    lookback = int(params.get("lookback", sig.get("lookback", 0)) or 0)
    periods = int(spec.get("warmup", lookback + int(spec.get("warmup_pad", 1))))
    offsets = [int(ic.get("offset", 1))
               for ic in (spec.get("regime") or {}).get("inputs", {}).values()]
    periods = max(periods, _BUNDLE_PERIODS + max(offsets, default=0))
    return max(periods * mpp, _BUNDLE_MONTHS, _expr_months(sig, lookback, params, mpp))


def _expr_months(sig: dict, lookback: int, params: dict, mpp: float) -> float:
    """Months of history the signal expression consumes before its first value.

    Resolved with the scalars `config_strategy.resolve_factor` evaluates it with,
    so `roll_sum(x, lookback)` reads the lookback the run will use. A factor that
    is a bare bundle key has no expression here; the bundle floor covers it.
    """
    entry = _signal_defs().get(sig.get("factor"), {}) if "expr" not in sig else {}
    expr = sig.get("expr") or entry.get("expr")
    if not expr:
        return 0.0
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return 0.0          # the run itself fails on it, with the parse error
    scalars = {**entry, **sig, "lookback": lookback, **params}
    return _span(tree.body, scalars, mpp)


def _span(node, scalars: dict, unit: float) -> float:
    """Months before `node` has its first value, `unit` months to a row.

    Windows nest by adding — `roll_sum(shift(x, 1), 12)` needs 13 rows — and
    branches by taking the longest. Inside `at_rebalance(...)` a row is a
    session, not a period.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return max((_span(c, scalars, unit) for c in ast.iter_child_nodes(node)),
                   default=0.0)
    fn, args = node.func.id, node.args
    inner_unit = _DAILY_MONTHS if fn == "at_rebalance" else unit
    need = max((_span(a, scalars, inner_unit) for a in args), default=0.0)
    if fn in _WINDOWED:
        window = _scalar(args[1], scalars) if len(args) > 1 else _DEFAULT_WINDOW.get(fn)
        need += max(window or 0.0, 0.0) * unit
    return need


def _scalar(node, scalars: dict) -> float | None:
    """`node` as a number — a literal, a scalar param, or arithmetic over them
    (`lookback * 21`) — or None when it is anything else."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.Name):
        value = scalars.get(node.id)
        return float(value) if type(value) in (int, float) else None
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
        left, right = _scalar(node.left, scalars), _scalar(node.right, scalars)
        if left is None or right is None or (right == 0 and isinstance(node.op, ast.Div)):
            return None
        return _ARITH[type(node.op)](left, right)
    return None


# ── What the run reports ──────────────────────────────────────────────────────

def _first_live(tw: pd.DataFrame):
    live = tw.index[tw.fillna(0.0).abs().sum(axis=1) > 0]
    return live[0] if len(live) else None


def _iso(date) -> str | None:
    return None if date is None else date.date().isoformat()


def late_books(name: str, tw: pd.DataFrame, shared) -> tuple[str | None, list]:
    """``(invested_from, warnings)`` for a run's in-window weights `tw`.

    `invested_from` is the first rebalance on which every book in the run holds
    something: the strategy's own and, in a blend, each strategy sleeve's — a
    sleeve still warming up has its share parked in CDI, which keeps the blend's
    rows non-empty and would otherwise hide it. None if some book never goes
    live in the window.

    A book that is not live on the window's first rebalance gets a
    `warmup_inside_window` warning: the history before `start` was too short to
    warm its signal up (or it held nothing there for a reason of its own — thin
    universe, a parked regime with no cash leg — which reads the same here).
    """
    opens = tw.index[0]
    books = {name: tw}
    for sleeve, book in (shared.get("_sleeve_books") or {}).items():
        books[sleeve] = in_window(book, opens)
    firsts = {k: _first_live(b) for k, b in books.items()}
    warnings = [{"code": WARNING,
                 "detail": {"strategy": k, "invested_from": _iso(d),
                            "first_rebalance": _iso(opens)}}
                for k, d in firsts.items() if d is None or d > opens]
    dates = list(firsts.values())
    invested = None if any(d is None for d in dates) else max(dates)
    return _iso(invested), warnings
