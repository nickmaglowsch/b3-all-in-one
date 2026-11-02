"""
Signal expression interpreter (the signal DSL)
=============================================
Turns a signal from *code* into *data*: a factor is a string expression over a
fixed, safe vocabulary of primitives, evaluated to a wide (date x ticker) frame
where **higher = better**. Named signals live in
``backtests/strategies/signals.yaml``; a strategy spec may also inline an
``expr:`` directly. Adding a new factor is a line of YAML — no Python.

Safety: the expression is parsed with the ``ast`` module and only a whitelist of
node types is allowed (arithmetic, numeric literals, whitelisted names, and
calls to whitelisted functions). There is **no** ``eval()`` of raw source, no
attribute access, no subscripting, no lambdas/comprehensions — so a malicious or
typo'd spec cannot execute arbitrary code. Whitelisted arithmetic can still be
*expensive*, so ``**`` additionally bounds a scalar exponent (see
``_MAX_EXPONENT``).

Namespace = every ``shared_data`` frame (``ret``, ``log_ret``, ``adtv``,
``vol_60d``, ``f_pe_ratio_dyn`` …) plus any numeric params (e.g. ``lookback``).

Parity note: rolling reductions default ``min_periods = window`` (plain
``.rolling(w)``), matching the original hand-written signals — NOT the
``max(k, w//2)`` used by research/discovery base_signals.
"""
from __future__ import annotations

import ast
import operator

import numpy as np
import pandas as pd

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_,   # boolean masks
}
_UNARYOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_CMPOPS = {
    ast.Gt: operator.gt, ast.Lt: operator.lt, ast.GtE: operator.ge,
    ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}

_ALLOWED = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
    ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd,
    ast.Compare, ast.BitAnd, ast.BitOr, *_CMPOPS,
)

# The allow-list stops arbitrary *code*; it does not stop arbitrary *arithmetic*.
# `9**9**9` is nothing but whitelisted nodes, and evaluates to a 370-million-digit
# integer — CPython allocates gigabytes and the worker dies. Signals raise to
# fractions and small powers (sqrt is `** 0.5`, variance is `** 2`), so a scalar
# exponent past this bound is a mistake or an attack, never a factor.
_MAX_EXPONENT = 64

# The exponent bound alone is not enough: `((9**64)**64)**64` keeps every
# exponent at 64 while the integer grows ~64x in bits per level. So any scalar
# integer an operation produces is bounded too; a few thousand bits is far past
# any real factor's arithmetic and still cheap to compute one more step from.
_MAX_SCALAR_BITS = 4096


def _zscore(x: pd.DataFrame) -> pd.DataFrame:
    m = x.mean(axis=1)
    s = x.std(axis=1).replace(0, np.nan)
    return x.sub(m, axis=0).div(s, axis=0)


# The cross-sectional three, per (date, sector) group; same semantics as rank /
# zscore / demean over a row (pct ranks, ddof=1, a zero spread is NaN).
_GROUPED = {
    "rank": lambda g, v: g.rank(pct=True),
    "demean": lambda g, v: v - g.transform("mean"),
    "zscore": lambda g, v: (v - g.transform("mean")) / g.transform("std").replace(0, np.nan),
}


def _sector_labels(sector_pit: pd.DataFrame, x: pd.DataFrame) -> pd.DataFrame:
    """Each of x's names' CVM sector as of each of x's dates: the latest filing on
    or before it (NaN before a name's first)."""
    wide = (sector_pit.sort_values("ref_date")
            .drop_duplicates(["ref_date", "ticker"], keep="last")
            .pivot(index="ref_date", columns="ticker", values="sector"))
    return (wide.reindex(wide.index.union(x.index)).ffill()
            .reindex(index=x.index, columns=x.columns))


def _build_funcs(ns: dict) -> dict:
    glitch = ns.get("has_glitch")
    rebal = ns.get("ret")          # the strategy's rebalance grid (rows x tickers)
    sector_pit = ns.get("sector_pit")

    def mask_glitch(x):
        return x.where(glitch != 1) if glitch is not None else x

    def _roll(x, w, agg):
        return getattr(x.rolling(int(w)), agg)()   # min_periods = window (parity)

    def at_rebalance(x):
        """Daily frame -> the rebalance grid: last observation of each period,
        columns aligned to the traded universe. Lets an expression mix a
        daily-derived factor with the monthly frames."""
        if rebal is None:
            return x
        freq = rebal.index.freq or pd.infer_freq(rebal.index)
        out = x.resample(freq).last() if freq is not None else x
        return out.reindex(columns=rebal.columns).reindex(rebal.index, method="ffill")

    def by_sector(how):
        """A cross-sectional op applied within each CVM sector instead of across
        the whole row — sector-neutral ranks and z-scores. A name with no
        sector on file comes out NaN (unranked), not pooled with strangers.
        One groupby over the stacked frame, not one pass per sector."""
        def apply(x):
            if sector_pit is None or sector_pit.empty:
                raise ValueError("signal expr: sector functions need CVM sector data, "
                                 "and this market plane has none (B3 only)")
            # Integer codes, not the strings: grouping on objects is the slow part.
            codes = pd.factorize(_sector_labels(sector_pit, x).to_numpy().ravel())[0]
            v = x.to_numpy(dtype=float).ravel()
            long = pd.DataFrame({"d": np.repeat(np.arange(len(x)), x.shape[1]),
                                 "s": codes, "v": v})
            long = long[long["s"] >= 0]                      # -1 = no sector on file
            g = long.groupby(["d", "s"])["v"]
            out = np.full(v.shape, np.nan)
            out[long.index] = _GROUPED[how](g, long["v"]).to_numpy()
            return pd.DataFrame(out.reshape(x.shape), index=x.index, columns=x.columns)
        return apply

    return {
        # time-series (operate per column, along time)
        "shift": lambda x, n: x.shift(int(n)),
        "roll_sum": lambda x, w: _roll(x, w, "sum"),
        "roll_mean": lambda x, w: _roll(x, w, "mean"),
        "roll_std": lambda x, w: _roll(x, w, "std"),
        "roll_var": lambda x, w: _roll(x, w, "var"),
        "roll_min": lambda x, w: _roll(x, w, "min"),
        "roll_max": lambda x, w: _roll(x, w, "max"),
        "roll_median": lambda x, w: _roll(x, w, "median"),
        "roll_skew": lambda x, w: _roll(x, w, "skew"),
        "roll_kurt": lambda x, w: _roll(x, w, "kurt"),
        "pct_change": lambda x, w=1: x.pct_change(int(w)),
        "diff": lambda x, n=1: x - x.shift(int(n)),
        "ewm_mean": lambda x, span: x.ewm(span=int(span), min_periods=int(span)).mean(),
        "ewm_std": lambda x, span: x.ewm(span=int(span), min_periods=int(span)).std(),
        # elementwise
        "log": lambda x: np.log(x),
        "sign": lambda x: np.sign(x),
        "abs": lambda x: x.abs() if hasattr(x, "abs") else abs(x),
        "clip": lambda x, lo, hi: x.clip(lo, hi),
        # cross-sectional (operate per row, across tickers)
        "rank": lambda x: x.rank(axis=1, pct=True),   # pandas semantics (parity)
        "zscore": _zscore,
        "demean": lambda x: x.sub(x.mean(axis=1), axis=0),
        # the same, within each CVM sector (B3)
        "sector_rank": by_sector("rank"),
        "sector_zscore": by_sector("zscore"),
        "sector_demean": by_sector("demean"),
        # data-quality / shaping
        "mask_glitch": mask_glitch,
        "where": lambda x, cond: x.where(cond),          # keep where cond, else NaN
        "nan_add": lambda a, b: a.add(b, fill_value=0),  # NaN only where BOTH are NaN
        "at_rebalance": at_rebalance,
    }


# The callable vocabulary, by name. The table is built per namespace (two of its
# entries close over the data), but its keys never depend on it.
FUNCTION_NAMES = frozenset(_build_funcs({}))


def _reject_disallowed_syntax(tree: ast.AST, expr: str) -> None:
    """Walk the AST and reject any node type outside the whitelist. Shared by
    evaluate() (before eval) and check_syntax() (data-free validation) so the
    allow-list lives in exactly one place."""
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"signal expr: disallowed syntax {type(node).__name__!r} in {expr!r}")


def check_syntax(expr: str) -> None:
    """Validate a signal expression against the AST whitelist *without* any
    ``shared_data`` — parse it and reject disallowed node types. Raises
    ``ValueError`` on a parse error or disallowed syntax; returns ``None`` when
    the expression is structurally safe to evaluate. Name/function existence is
    NOT checked here (that needs the market-data namespace); ``evaluate()``
    enforces it at run time."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"signal expr: parse error in {expr!r}: {e.msg}") from e
    _reject_disallowed_syntax(tree, expr)
    _fold_constant_arithmetic(tree.body, expr)


def _fold_constant_arithmetic(node, expr: str) -> None:
    """Evaluate every data-free subtree now, so the exponent and scalar-size
    bounds that _ev enforces at run time also reject at save time."""
    if (isinstance(node, ast.BinOp)
            and not any(isinstance(n, (ast.Name, ast.Call)) for n in ast.walk(node))):
        try:
            _ev(node, {}, {})
        # TypeError: a constant the grammar admits but Python won't compute,
        # `1.5 | 2`. A 422 at save, not a 500.
        except (ArithmeticError, TypeError) as e:
            raise ValueError(f"signal expr: {e} in {expr!r}") from e
        return
    for child in ast.iter_child_nodes(node):
        _fold_constant_arithmetic(child, expr)


def evaluate(expr: str, namespace) -> pd.DataFrame:
    """Evaluate a signal expression string against ``namespace`` (name -> frame or
    scalar). Returns a wide DataFrame. Raises ValueError on disallowed syntax or
    unknown names/functions.

    ``namespace`` is any mapping, not necessarily a dict — the interpreter only
    ever asks ``name in ns`` and ``ns[name]``, and _build_funcs only ``.get()``.
    That is load-bearing: the shared_data bundle builds its daily panels on first
    read (backtests.core.memory), and config_strategy hands this a ChainMap over
    it precisely so evaluating `-atr_m` builds the ATR proxy and nothing else.
    Flattening the bundle into a dict here would undo that for every spec."""
    tree = ast.parse(expr, mode="eval")
    _reject_disallowed_syntax(tree, expr)
    funcs = _build_funcs(namespace)
    return _ev(tree.body, namespace, funcs)


def _bounded_scalar(out):
    if isinstance(out, int) and not isinstance(out, bool) and out.bit_length() > _MAX_SCALAR_BITS:
        raise ValueError(
            f"signal expr: scalar of {out.bit_length()} bits exceeds the maximum "
            f"of {_MAX_SCALAR_BITS} (nested exponentiation is not allowed)")
    return out


def _ev(node, ns, funcs):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError(f"signal expr: only numeric constants allowed, got {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in ns:
            return ns[node.id]
        raise ValueError(f"signal expr: unknown name {node.id!r}")
    if isinstance(node, ast.UnaryOp):
        return _UNARYOPS[type(node.op)](_ev(node.operand, ns, funcs))
    if isinstance(node, ast.BinOp):
        left, right = _ev(node.left, ns, funcs), _ev(node.right, ns, funcs)
        # Only a *scalar* exponent is bounded — a frame exponent is elementwise,
        # so its cost is the frame's size, which is already bounded.
        if (isinstance(node.op, ast.Pow) and isinstance(right, (int, float))
                and abs(right) > _MAX_EXPONENT):
            raise ValueError(
                f"signal expr: exponent {right!r} exceeds the maximum of {_MAX_EXPONENT}")
        return _bounded_scalar(_BINOPS[type(node.op)](left, right))
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise ValueError(f"signal expr: chained comparisons are not allowed in {ast.dump(node)!r}")
        return _CMPOPS[type(node.ops[0])](
            _ev(node.left, ns, funcs), _ev(node.comparators[0], ns, funcs))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ValueError("signal expr: only positional calls to named functions are allowed")
        fn = funcs.get(node.func.id)
        if fn is None:
            raise ValueError(f"signal expr: unknown function {node.func.id!r}")
        return fn(*[_ev(a, ns, funcs) for a in node.args])
    raise ValueError(f"signal expr: disallowed node {type(node).__name__!r}")
