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


def _combine_legs(cfg: dict, shared, params: dict | None) -> pd.DataFrame:
    """A weighted sum of legs, each a factor or expr of its own. ``coverage:
    all`` (default) scores only names every leg covers; ``any`` scores a name on
    the legs it has, as a weighted mean — the two_leg_value case, where P/B and
    ROE come from different tables and a plain sum drops every name missing one.

    Each leg reads the signal's scalars (``lookback`` and any extra numbers)."""
    base = {k: v for k, v in cfg.items() if k not in ("legs", "vars", "expr", "factor")}
    num = den = None
    for leg in cfg["legs"]:
        x = resolve_factor({**base, **leg}, shared, params)
        x = _LEG_TRANSFORMS[leg.get("transform", "rank")](x)
        w = float(leg.get("weight", 1.0))
        if cfg.get("coverage", "all") == "all":
            num = w * x if num is None else num + w * x      # NaN where any leg is
            continue
        part, seen = (w * x).fillna(0.0), x.notna() * abs(w)
        num = part if num is None else num.add(part, fill_value=0.0)
        den = seen if den is None else den.add(seen, fill_value=0.0)
    return num if den is None else num / den.where(den > 0)


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
    return resolve_factor({**cfg, "expr": spec["weight_expr"]}, shared, params)


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
    """Trade only on the first grid row of each listed calendar month; every other
    row is all-zero, which run_simulation holds (drifts) rather than rebalances.
    So `rebalance: ME` + `rebalance_months: [6, 12]` is a semiannual book, and the
    rows it trades on are fixed by the calendar — not by where the window starts."""
    if not months:
        return tw
    idx = tw.index
    first = ~pd.Series(idx.to_period("M"), index=idx).duplicated().to_numpy()
    tw.loc[~(idx.month.isin(list(months)) & first)] = 0.0
    return tw
