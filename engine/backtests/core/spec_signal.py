"""
The spec language's signal half
===============================
What a rank_and_hold spec's ``signal`` block (and its ``weight_expr``,
``universe.sectors`` and ``rebalance_months``) turns into: frames over
``shared_data``, built by the signal DSL (signal_dsl.py). config_strategy owns
the loop that ranks, selects and weights; this module owns what it ranks on.

Split out of config_strategy.py as the language grew legs and vars, so that the
engine module stays about the engines.
"""
from __future__ import annotations

import functools
from collections import ChainMap
from pathlib import Path

import numpy as np
import pandas as pd

from backtests.core import signal_dsl
from backtests.core.data import sector_membership_asof


# ── Signals as data ───────────────────────────────────────────────────────────
# A factor is a string expression (the signal DSL, see signal_dsl.py) over
# shared_data frames. Named signals live in backtests/strategies/signals.yaml;
# specs may also inline an `expr:`. resolve_factor / _build_signal wire it in.

@functools.lru_cache(maxsize=1)
def _signal_defs() -> dict:
    """Load the named-signal definitions from backtests/strategies/signals.yaml
    (factor name -> {expr, <default params>})."""
    import yaml
    path = Path(__file__).resolve().parent.parent / "strategies" / "signals.yaml"
    return yaml.safe_load(path.read_text()) or {}


def _eval_expr(expr: str, scalars: dict, shared: dict) -> pd.DataFrame:
    """A signal expression over the shared_data frames plus any numeric scalars —
    chained, not copied, and scalars win. See signal_dsl.evaluate for why."""
    ns = {k: v for k, v in scalars.items() if isinstance(v, (int, float))}
    return signal_dsl.evaluate(expr, ChainMap(ns, shared))


def _with_vars(cfg: dict, scalars: dict, shared):
    """``shared`` plus the cfg's ``vars`` — named sub-expressions, evaluated in
    order, each able to read the ones before it. The DSL has no local bindings,
    so without these a factor used twice is written (and computed) twice."""
    local: dict = {}
    for name, expr in (cfg.get("vars") or {}).items():
        local[name] = _eval_expr(expr, scalars, ChainMap(local, shared))
    return ChainMap(local, shared) if local else shared


# How a leg is put on a common scale before the legs are summed.
_LEG_TRANSFORMS = {
    "rank": lambda x: x.rank(axis=1, pct=True),
    "zscore": signal_dsl._zscore,
    "raw": lambda x: x,
}


def _frame(x, what: str) -> pd.DataFrame:
    """A leg or weight_expr has to be a (date x ticker) frame; a bare number
    (`1`, `lookback`) is a spec mistake, said here instead of as an AttributeError
    from the selection loop."""
    if not isinstance(x, pd.DataFrame):
        raise ValueError(f"{what} must read market data (a date x ticker frame), "
                         f"got {type(x).__name__}")
    return x


def _neutral(x: pd.DataFrame) -> pd.DataFrame:
    """Missing cells as the row's average: ~0.5 for a rank, 0 for a z-score. A
    row the leg does not cover at all is the same constant for every name."""
    return x.T.fillna(x.mean(axis=1)).T.fillna(0.0)


def _combine_legs(cfg: dict, shared, params: dict | None) -> pd.DataFrame:
    """A weighted sum of legs, each a factor or expr of its own. ``coverage:
    all`` (default) scores only names every leg covers; ``any`` scores a name on
    the legs it has — the two_leg_value case, where P/B and ROE come from
    different tables and a plain sum drops every name missing one. A leg a name
    lacks counts as that leg's average on the date, whatever the sign of the
    leg's weight — which pulls the name toward the middle, so a top-N picks it
    slightly less often than a name with every leg.

    Each leg reads the signal's scalars (``lookback`` and any extra numbers)."""
    base = {k: v for k, v in cfg.items() if k not in ("legs", "vars", "expr", "factor")}
    legs = []
    for leg in cfg["legs"]:
        x = _frame(resolve_factor({**base, **leg}, shared, params), "a signal leg")
        legs.append((float(leg.get("weight", 1.0)),
                     _LEG_TRANSFORMS[leg.get("transform", "rank")](x)))
    if cfg.get("coverage", "all") == "all":
        return sum(w * x for w, x in legs)               # NaN where any leg is
    idx = functools.reduce(pd.Index.union, (x.index for _, x in legs))
    cols = functools.reduce(pd.Index.union, (x.columns for _, x in legs))
    legs = [(w, x.reindex(index=idx, columns=cols)) for w, x in legs]
    covered = sum(x.notna().astype(int) for _, x in legs) > 0
    return sum(w * _neutral(x) for w, x in legs).where(covered)


def resolve_factor(cfg: dict, shared: dict, params: dict | None = None) -> pd.DataFrame:
    """Resolve one factor cfg to a wide (higher=better) frame. In precedence:

    1. ``legs:`` — a weighted blend of sub-factors (_combine_legs);
    2. an inline ``expr:`` string (the signal DSL);
    3. a named signal from ``signals.yaml`` (also a DSL ``expr``);
    4. a bare ``shared_data`` key (e.g. ``mf_composite``, ``f_pe_ratio_dyn``).

    ``params`` (UI/default values) override the signal's own defaults, so a
    ``lookback`` slider flows into the expression. ``vars:`` names
    sub-expressions every one of the above can read.
    """
    scalars = {**cfg, **(params or {})}
    if cfg.get("vars"):
        shared = _with_vars(cfg, scalars, shared)
        cfg = {k: v for k, v in cfg.items() if k != "vars"}
    if "legs" in cfg:
        return _combine_legs(cfg, shared, params)
    if "expr" in cfg:
        return _eval_expr(cfg["expr"], scalars, shared)

    ref = cfg.get("factor")
    defs = _signal_defs()
    if ref in defs:
        entry = defs[ref]
        merged = {**entry, **scalars}       # spec/params override the signal's defaults
        return _eval_expr(entry["expr"], merged, shared)
    if ref in shared:
        return shared[ref]
    raise KeyError(
        f"Unknown factor {ref!r}: not an expr, a name in signals.yaml, "
        f"or a shared_data key"
    )


def _signal_cfg(spec: dict, params: dict) -> dict:
    cfg = {**spec["signal"]}
    cfg["lookback"] = params.get("lookback", cfg.get("lookback", 0))
    return cfg


def _build_signal(spec: dict, params: dict, shared: dict) -> tuple[pd.DataFrame, int]:
    """Return (signal_df, lookback). The signal is a factor, an expression or a
    blend of ``legs``; a composite can also be one ``expr`` using
    ``rank()``/``zscore()`` (see specs/multifactor.yaml)."""
    cfg = _signal_cfg(spec, params)
    sig = resolve_factor(cfg, shared, params)
    if cfg.get("apply_glitch", True):
        glitch = shared.get("has_glitch")
        if glitch is not None:
            sig = sig.where(glitch != 1)
    return sig, int(cfg.get("lookback", 0) or 0)


def _weight_frame(spec: dict, params: dict, shared: dict) -> pd.DataFrame | None:
    """``weight_expr`` evaluated like the signal — same scalars, same ``vars`` —
    to the frame a held name's weight is proportional to; None unless the spec
    weights by ``expr``."""
    if spec.get("weighting") != "expr":
        return None
    cfg = {k: v for k, v in _signal_cfg(spec, params).items()
           if k not in ("factor", "expr", "legs")}
    return _frame(resolve_factor({**cfg, "expr": spec["weight_expr"]}, shared, params),
                  "weight_expr")


def sector_filter(sectors: dict | None, shared: dict, index) -> pd.DataFrame:
    """Which names the universe's ``sectors`` admits on each grid date (all of
    them when it names no keyword): the latest
    CVM sector filed on or before it matches an ``include`` keyword (if any are
    given) and no ``exclude`` keyword. Keywords match case-insensitive substrings
    of CVM's SETOR_ATIV, as data.sector_membership_asof does for the hand-written
    sector strategies. A name with no sector on file fails ``include``."""
    cols = shared["ret"].columns
    ok = pd.DataFrame(True, index=index, columns=cols)
    sectors = sectors or {}
    if not (sectors.get("include") or sectors.get("exclude")):
        return ok
    pit = shared.get("sector_pit")
    if pit is None or pit.empty:
        raise ValueError("universe.sectors needs CVM sector data, and this market "
                         "plane has none (B3 only)")
    if sectors.get("include"):
        ok &= sector_membership_asof(pit, sectors["include"], index).reindex(
            columns=cols, fill_value=False)
    if sectors.get("exclude"):
        ok &= ~sector_membership_asof(pit, sectors["exclude"], index).reindex(
            columns=cols, fill_value=False)
    return ok


def only_in_months(tw: pd.DataFrame, months) -> pd.DataFrame:
    """Trade only on the first grid row on or after the 1st of each listed
    calendar month; every other row is all-zero, which run_simulation holds
    (drifts) rather than rebalances. So `rebalance: ME` + `rebalance_months:
    [6, 12]` is a semiannual book, and the rows it trades on are fixed by the
    calendar — not by where the window starts. A grid with no row in a listed
    month (a run overriding the spec's ME with QE) trades on the next row."""
    if not months:
        return tw
    idx = tw.index
    first = idx[0].to_period("M").start_time
    starts = [pd.Timestamp(y, m, 1) for y in range(idx[0].year, idx[-1].year + 1)
              for m in months]
    pos = idx.searchsorted([t for t in starts if t >= first])
    keep = np.zeros(len(idx), dtype=bool)
    keep[pos[pos < len(idx)]] = True
    tw.loc[~keep] = 0.0
    return tw
