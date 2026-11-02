"""How much history a strategy's signals consume before their first value.

The sizing half of backtests.core.warmup, kept apart because it is a reading of
strategy *definitions* — specs, signal expressions, parameter names — and the
other half is about frames at run time.

Everything here is in months and errs long: a pre-roll a few months too long
costs a little memory, one a row too short puts the warm-up back inside the
window, which is the bug this exists to fix.
"""
from __future__ import annotations

import ast
import operator

from backtests.core.config_strategy import FixedWeight, RankAndHold, _signal_defs

# A hand-written strategy keeps its warm-up in a loop this module cannot read
# (`range(lookback + 2, ...)`, a three-year fundamentals window, a 400-day SMA).
# It gets this floor, stretched by whatever look-back parameters it exposes
# (`_code_months`), since QMV at QE with lookback 36 needs nearly ten years.
CODE_MONTHS = 36.0

_MONTHS_PER_PERIOD = {"ME": 1.0, "QE": 3.0, "W-FRI": 7 / 30.44}
# One session, in months. 21 is a B3 month; a 30-session crypto month makes
# this an overestimate, the safe side.
_SESSION = 1 / 21

# Rows a bundle key consumes before its first value, as (grid periods, sessions):
# what shared_data / regimes / mean_rev_helpers do to build it. A key used inside
# a window adds this to the window — `roll_mean(mf_composite, 12)` is 13 + 11
# rows, not 11. Keys not listed (prices, volumes, CDI, the `f_*` frames) cost
# nothing of their own.
_LEAF_COST = {
    **dict.fromkeys(("ret", "log_ret", "ibov_ret", "bench_ret"), (1, 0)),
    "has_glitch": (12, 0), "mf_composite": (13, 0), "is_easing": (4, 0),
    **dict.fromkeys(("vol_5m", "vol_60d"), (5, 0)),
    **dict.fromkeys(("vol_2m", "vol_20d"), (2, 0)),
    **dict.fromkeys(("ibov_above", "bench_above"), (10, 0)),
    **dict.fromkeys(("ibov_uptrend", "bench_uptrend", "ibov_vol_monthly",
                     "bench_vol_monthly"), (1, 21)),
    **dict.fromkeys(("ibov_vol_pctrank", "bench_vol_pctrank", "ibov_calm",
                     "bench_calm"), (12, 21)),
    **dict.fromkeys(("ma200_m", "above_ma200", "dist_ma200"), (1, 200)),
    **dict.fromkeys(("atr_m", "atr_20d_daily"), (1, 15)),
    **dict.fromkeys(("autocorr_20d", "rolling_vol_20d_daily", "high_low_range_20d",
                     "ibov_vol_20d_daily", "ibov_ret_20d_daily"), (0, 22)),
    **dict.fromkeys(("autocorr_60d", "rolling_vol_60d_daily", "cdi_cumul_63d"),
                    (0, 64)),
    # Prices a year back, to put each dividend over the price it was paid at.
    "div_yield_12m": (0, 252),
}

# DSL functions whose second argument is a look-back: how many rows a window of
# `w` consumes (a rolling window's first value lands on its w-th row), and the
# window when none is given.
_ROLLING = {"roll_sum", "roll_mean", "roll_std", "roll_var", "roll_min", "roll_max",
            "roll_median", "roll_skew", "roll_kurt", "ewm_mean", "ewm_std"}
_LAGGING = {"shift", "pct_change", "diff"}
_DEFAULT_WINDOW = {"pct_change": 1.0, "diff": 1.0}
_ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv}


def strategy_months(strat, params: dict, freq: str) -> float:
    """Months of history `strat` reads before its first weight, unrounded."""
    return _months(strat, params, _MONTHS_PER_PERIOD.get(freq, 1.0))


def _months(strat, params: dict, mpp: float) -> float:
    if isinstance(strat, FixedWeight):
        need = _blend_months(strat.spec, params, mpp)
    elif isinstance(strat, RankAndHold):
        need = _ranker_months(strat.spec, params, mpp)
    else:
        return _code_months(params, mpp)
    return need + _month_gap(strat.spec.get("rebalance_months"))


def _month_gap(months) -> float:
    """A `rebalance_months` book on `start` is the one bought in the listed month
    before it — up to a year back — so the pre-roll has to reach that month too,
    or the run opens in cash where a longer one is invested."""
    if not months:
        return 0.0
    m = sorted(set(months))
    return float(max((b - a) % 12 or 12 for a, b in zip(m, m[1:] + m[:1])))


def _code_months(params: dict, mpp: float) -> float:
    """The floor, or longer when a look-back parameter asks for it: `lookback`
    in grid periods (+2, QMV's `range(lookback + 2, ...)`), `*_months` in
    months, `*_days` / `ewma_n` / `sma_window` in sessions."""
    need = CODE_MONTHS
    for key, value in params.items():
        if type(value) not in (int, float):
            continue
        if key == "lookback" or key.endswith("_periods"):
            need = max(need, (value + 2) * mpp)
        elif key.endswith("_months"):
            need = max(need, value + 2 * mpp)
        elif key.endswith("_days") or key in ("ewma_n", "sma_window"):
            need = max(need, value * _SESSION + 2 * mpp)
    return need


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
            need = max(need, _months(sub, sub_params, mpp))
    return need


def _ranker_months(spec: dict, params: dict, mpp: float) -> float:
    """A rank_and_hold spec's warm-up, the longest of: the rows its own loop
    skips (`warmup`, or lookback + warmup_pad); what its regime flags need before
    the offset it reads them at; and its expression, plus the row the weight at
    i reads it from (i - 1).

    The expression is not the whole signal. `_build_signal` masks it with
    `has_glitch` unless `apply_glitch: false`, and that mask is a 12-period
    rolling max that masks nothing until it has its twelve rows — so a 6-month
    low-vol book loaded from 10 months back held a reverse split's fake return
    that a longer run screens out. The mask is costed like the expression.
    """
    sig = spec.get("signal", {})
    lookback = int(params.get("lookback", sig.get("lookback", 0)) or 0)
    periods = int(spec.get("warmup", lookback + int(spec.get("warmup_pad", 1))))
    regime = (spec.get("regime") or {}).get("inputs", {})
    flags = [_leaf(ic.get("flag"), mpp) + int(ic.get("offset", 1)) * mpp
             for ic in regime.values()]
    signal = _expr_months(sig, lookback, params, mpp)
    if spec.get("weight_expr"):
        wsig = {k: v for k, v in sig.items() if k not in ("legs", "factor")}
        signal = max(signal, _expr_months({**wsig, "expr": spec["weight_expr"]},
                                          lookback, params, mpp))
    if sig.get("apply_glitch", True):
        signal = max(signal, _leaf("has_glitch", mpp))
    return max(periods * mpp, *flags, signal + mpp)


def _expr_months(sig: dict, lookback: int, params: dict, mpp: float) -> float:
    """Months of history the signal expression consumes before its first value.

    Resolved with the scalars `config_strategy.resolve_factor` evaluates it with,
    so `roll_sum(x, lookback)` reads the lookback the run will use. A factor that
    is neither an expression nor a named signal is a bundle key, costed as one.
    A ``vars`` name costs what its expression does; ``legs`` cost their longest.
    """
    var_months: dict = {}
    for name, expr in (sig.get("vars") or {}).items():
        var_months[name] = _parsed_months(expr, {**sig, "lookback": lookback, **params},
                                          mpp, var_months)
    if "legs" in sig:
        base = {k: v for k, v in sig.items() if k not in ("legs", "vars", "expr", "factor")}
        return max((_one_months({**base, **leg}, lookback, params, mpp, var_months)
                    for leg in sig["legs"]), default=0.0)
    return _one_months(sig, lookback, params, mpp, var_months)


def _one_months(sig: dict, lookback: int, params: dict, mpp: float,
                var_months: dict) -> float:
    entry = _signal_defs().get(sig.get("factor"), {}) if "expr" not in sig else {}
    expr = sig.get("expr") or entry.get("expr")
    if not expr:
        name = sig.get("factor")
        return var_months[name] if name in var_months else _leaf(name, mpp)
    return _parsed_months(expr, {**entry, **sig, "lookback": lookback, **params},
                          mpp, var_months)


def _parsed_months(expr: str, scalars: dict, mpp: float, var_months: dict) -> float:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return 0.0          # the run itself fails on it, with the parse error
    return _Span(scalars, mpp, var_months).months(tree.body, mpp)


def _leaf(name, mpp: float) -> float:
    periods, sessions = _LEAF_COST.get(name, (0, 0))
    return periods * mpp + sessions * _SESSION


class _Span:
    """Months before an expression node has its first value.

    Windows nest by adding — `roll_sum(shift(log_ret, 1), 12)` is 1 + 1 + 11
    rows — and branches take the longest. A row is a grid period, except inside
    `at_rebalance(...)`, where the frames are daily and a row is a session.
    """

    def __init__(self, scalars: dict, mpp: float, var_months: dict | None = None) -> None:
        self.scalars, self.mpp, self.var_months = scalars, mpp, var_months or {}

    def months(self, node, unit: float) -> float:
        if isinstance(node, ast.Name):
            if node.id in self.var_months:
                return self.var_months[node.id]
            return _leaf(node.id, self.mpp)
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            return max((self.months(c, unit) for c in ast.iter_child_nodes(node)),
                       default=0.0)
        fn, args = node.func.id, node.args
        inner = _SESSION if fn == "at_rebalance" else unit
        need = max((self.months(a, inner) for a in args), default=0.0)
        if fn == "mask_glitch":     # reads has_glitch, which is not an argument
            need = max(need, _leaf("has_glitch", self.mpp))
        return need + self._rows(fn, args) * unit

    def _rows(self, fn: str, args) -> float:
        if fn not in _ROLLING and fn not in _LAGGING:
            return 0.0
        window = _scalar(args[1], self.scalars) if len(args) > 1 else _DEFAULT_WINDOW.get(fn)
        window = max(window or 0.0, 0.0)
        return max(window - 1, 0.0) if fn in _ROLLING else window


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
