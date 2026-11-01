"""Validate a user-authored strategy spec — no backtest run.

Usage:
    python scripts/validate_spec.py --spec-json '<json>'

<json> is an engine spec dict (the same schema strategy_registry.discover()
loads from specs/*.yaml). Constructs the matching config engine (RankAndHold /
FixedWeight), runs get_parameter_specs(), and — for a rank_and_hold signal —
validates the factor: check_syntax on an inline expr:, resolves a factor:
reference against signals.yaml or the whitelisted shared_data keys, rejects
store:<id>, and flags needs_fundamentals when any f_* key is referenced.

For a fixed_weight spec it checks each sleeve's shape, and — the one step that
reads the market plane (ENGINE_DATABASE_URL) — that every `ticker` sleeve names
an instrument the B3 plane has prices for (backtests.core.sleeve_tickers).

Prints one JSON line {"ok": bool, "errors": [...], "warnings": [...],
"needs_fundamentals": bool} to stdout, exit 0 when ok else 1. The platform
shells this on save and returns the warnings with the saved strategy.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.core import dividends, markets, signal_dsl, sleeve_tickers
from backtests.core.config_strategy import RankAndHold, FixedWeight, _signal_defs
from backtests.core.spec_signal import _LEG_TRANSFORMS

# shared_data frames a bare `factor:` reference may name, per
# docs/config_strategies.md "Primitive vocabulary". Fundamentals `f_*` keys are
# open-ended (matched by prefix) and imply needs_fundamentals.
SHARED_DATA_KEYS = {
    "ret", "log_ret", "adj_close", "raw_close", "adtv", "close_px",
    "fin_vol", "vol_60d", "vol_20d", "atr_m", "mf_composite",
    # Market-neutral aliases for the benchmark frames; see shared_data.
    "bench_ret", "bench_px",
    # Dividends (backtests.core.dividends) — B3 only, see _B3_ONLY_NAMES.
    *dividends.KEYS,
}

# What only B3's plane has: dividends (corporate_actions) and CVM sectors.
_B3_ONLY_NAMES = set(dividends.KEYS)
_B3_ONLY_FUNCS = {"sector_rank", "sector_zscore", "sector_demean"}

_KINDS = {"rank_and_hold": RankAndHold, "fixed_weight": FixedWeight}


def _expr_names(expr: str) -> set[str]:
    """The bare Name identifiers an expression reads (function names excluded — a
    Call's func is a Name but resolves against the DSL func table, not data)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - called


def _check_expr(expr: str, errors: list[str]) -> bool:
    """Syntax, then the functions it calls — a closed set, so an unknown one is
    a typo caught here rather than a backtest that fails minutes later. Returns
    whether the expression reads CVM fundamentals (f_* keys)."""
    try:
        signal_dsl.check_syntax(expr)
    except ValueError as e:
        errors.append(str(e))
        return False
    tree = ast.parse(expr, mode="eval")
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    if unknown := sorted(called - signal_dsl.FUNCTION_NAMES):
        errors.append(f"signal expr: unknown function(s) {unknown}; available: "
                      f"{sorted(signal_dsl.FUNCTION_NAMES)}")
    return any(n.startswith("f_") for n in _expr_names(expr))


# What a regime rule may say besides naming its inputs (config_strategy._eval_regime).
_RULE_KEYS = {"top_pct", "park", "default", "min_true"}


def _validate_regime(regime, errors: list[str]) -> None:
    """The overlay's structure: every input reads a flag, every rule conditions
    only on inputs it declares. A rule keyed on a misspelt input otherwise never
    matches and the book silently parks in CDI for the whole run."""
    if not regime:
        return
    inputs = regime.get("inputs", {}) if isinstance(regime, dict) else None
    if not isinstance(inputs, dict) or not isinstance(regime.get("rules"), list):
        errors.append("regime: needs an `inputs` mapping and a `rules` list")
        return
    for name, cfg in inputs.items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("flag"), str):
            errors.append(f"regime input {name!r}: needs a `flag` naming a shared_data "
                          "series (e.g. bench_above)")
    for rule in regime["rules"]:
        extra = set(rule) - set(inputs) - _RULE_KEYS if isinstance(rule, dict) else {"?"}
        if extra:
            errors.append(f"regime rule {rule!r}: {sorted(extra)} is not a declared input")


def _validate_sleeves(spec: dict, errors: list[str]) -> None:
    """Each fixed_weight sleeve is exactly one of a `ticker`, the `asset` CDI or a
    nested `strategy`, with a numeric weight. FixedWeight branches on those keys,
    so a sleeve carrying two would silently run as whichever it tests first, and
    one carrying none would fail minutes into a backtest instead of here."""
    sleeves = spec.get("sleeves")
    if not isinstance(sleeves, list) or not sleeves:
        errors.append("fixed_weight: `sleeves` must be a non-empty list")
        return
    for i, sl in enumerate(sleeves):
        kinds = [k for k in ("ticker", "asset", "strategy") if k in sl] \
            if isinstance(sl, dict) else []
        weight = sl.get("weight") if isinstance(sl, dict) else None
        if len(kinds) != 1 or (kinds == ["asset"] and sl["asset"] != "CDI"):
            errors.append(f"sleeve {i}: needs exactly one of ticker / asset: CDI / "
                          f"strategy, got {sl!r}")
        elif isinstance(weight, bool) or not isinstance(weight, (int, float)):
            errors.append(f"sleeve {i}: needs a numeric `weight`")


def _validate_rank_signal(spec: dict, errors: list[str]) -> bool:
    """Validate a rank_and_hold spec's signal block — its vars, its legs or its
    one factor/expr — and its weight_expr, appending any errors. Returns whether
    any of them references CVM fundamentals (f_* keys)."""
    sig = spec.get("signal") or {}
    local = sig.get("vars") or {}
    if not isinstance(local, dict):
        errors.append("signal.vars must map names to expressions")
        local = {}
    funds = _validate_vars(local, errors)
    if "legs" in sig:
        funds |= _validate_legs(sig, errors, local)
    else:
        funds |= _validate_factor(sig, errors, local)
    if spec.get("weighting") == "expr":
        if isinstance(spec.get("weight_expr"), str):
            funds |= _check_expr(spec["weight_expr"], errors)
        else:
            errors.append("weighting `expr` needs a `weight_expr` expression")
    return funds


def _validate_vars(local: dict, errors: list[str]) -> bool:
    funds = False
    for name, expr in local.items():
        if not name.isidentifier() or name in signal_dsl.FUNCTION_NAMES:
            errors.append(f"signal.vars: {name!r} must be a plain name that is not "
                          "a function")
        if isinstance(expr, str):
            funds |= _check_expr(expr, errors)
        else:
            errors.append(f"signal.vars.{name}: must be an expression string")
    return funds


def _validate_legs(sig: dict, errors: list[str], local: dict) -> bool:
    legs = sig["legs"]
    if not isinstance(legs, list) or not legs:
        errors.append("signal.legs must be a non-empty list")
        legs = []
    if sig.get("coverage", "all") not in ("all", "any"):
        errors.append("signal.coverage must be `all` or `any`")
    funds = False
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict) or ("factor" in leg) == ("expr" in leg):
            errors.append(f"signal.legs[{i}]: needs exactly one of factor / expr")
        elif leg.get("transform", "rank") not in _LEG_TRANSFORMS:
            errors.append(f"signal.legs[{i}]: transform must be one of "
                          f"{sorted(_LEG_TRANSFORMS)}")
        else:
            funds |= _validate_factor(leg, errors, local)
    return funds


def _validate_factor(sig: dict, errors: list[str], local: dict) -> bool:
    """One factor source — an inline expr, a named signal, a bundle key or one of
    the signal's own vars. Returns whether it reads fundamentals."""
    if "expr" in sig:
        return _check_expr(sig["expr"], errors)

    ref = sig.get("factor")
    if ref in local:
        return False                                   # its expr was checked above
    if isinstance(ref, str) and ref.startswith("store:"):
        errors.append(f"factor {ref!r}: store: references are not allowed for user strategies")
        return False
    defs = _signal_defs()
    if ref in defs:
        # A named signal is an expr like any other, so it can read fundamentals
        # too (book_to_price_pit and the margins do). Returning False here meant
        # a spec referencing one by name was never flagged needs_fundamentals —
        # and so slipped past the "this market has no fundamentals" guard that
        # validate() applies below.
        return any(n.startswith("f_") for n in _expr_names(defs[ref].get("expr", "")))
    if isinstance(ref, str) and ref.startswith("f_"):
        return True                                    # a fundamentals shared_data frame
    if ref in SHARED_DATA_KEYS:
        return False
    errors.append(
        f"unknown factor {ref!r}: not a signal in signals.yaml nor a whitelisted "
        f"shared_data key")
    return False


def validate(spec: dict) -> dict:
    """Return {"ok", "errors", "needs_fundamentals"} for a spec dict. Pure — no
    market data, no side effects — so both main() and the tests call it."""
    errors: list[str] = []
    needs_funds = False

    kind = spec.get("kind")
    cls = _KINDS.get(kind)
    if cls is None:
        errors.append(f"unknown kind {kind!r}: expected one of {sorted(_KINDS)}")
    else:
        try:
            cls(spec).get_parameter_specs()
        except Exception as e:
            errors.append(f"invalid spec: {e}")
        if kind == "rank_and_hold":
            needs_funds = _validate_rank_signal(spec, errors)
            _validate_regime(spec.get("regime"), errors)
        else:
            _validate_sleeves(spec, errors)

    needs_funds = needs_funds or bool(spec.get("needs_fundamentals"))

    # An unknown market must be rejected here rather than defaulting to B3: a
    # tenant saving `market: USA` would otherwise get a silently Brazilian
    # backtest and no way to tell.
    try:
        spec_market = markets.get(spec.get("market"))
    except ValueError as e:
        errors.append(str(e))
    else:
        if needs_funds and not spec_market.fundamentals:
            errors.append(
                f"market {spec_market.key} has no fundamentals: a spec that reads"
                " f_* signals cannot run against it")
        if spec_market.key != "B3" and (used := _b3_only(spec)):
            errors.append(f"{', '.join(used)}: B3 only (dividends and CVM sectors) — "
                          f"market {spec_market.key} has no such data")

    return {"ok": not errors, "errors": errors, "needs_fundamentals": needs_funds}


def _b3_only(spec: dict) -> list[str]:
    """The B3-only data a rank spec reads: a sector filter, a dividend frame or a
    sector function, wherever in the spec it is named."""
    if spec.get("kind") != "rank_and_hold":
        return []
    sig = spec.get("signal") or {}
    sectors = (spec.get("universe") or {}).get("sectors") or {}
    used = ["universe.sectors"] if sectors.get("include") or sectors.get("exclude") else []
    legs = sig.get("legs") if isinstance(sig.get("legs"), list) else []
    sources = [sig, *[leg for leg in legs if isinstance(leg, dict)]]
    local = sig.get("vars") if isinstance(sig.get("vars"), dict) else {}
    exprs = [x for x in local.values() if isinstance(x, str)]
    exprs += [src["expr"] for src in sources if isinstance(src.get("expr"), str)]
    if isinstance(spec.get("weight_expr"), str):
        exprs.append(spec["weight_expr"])
    names = {src.get("factor") for src in sources}
    for expr in exprs:
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            continue
        names |= _expr_names(expr)
        names |= {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return used + sorted(names & (_B3_ONLY_NAMES | _B3_ONLY_FUNCS))


def report(spec: dict) -> dict:
    """validate(), then the spec's ticker sleeves against the market plane.

    Apart from validate() so that one stays pure. The plane is read only when the
    spec is otherwise sound and has a ticker sleeve, so every other save costs no
    query at all.
    """
    out = {**validate(spec), "warnings": []}
    if out["ok"] and spec.get("kind") == "fixed_weight":
        errors, out["warnings"] = sleeve_tickers.check(spec)
        out["errors"] += errors
        out["ok"] = not out["errors"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec-json", required=True, help="engine spec dict as JSON")
    args = ap.parse_args()

    try:
        spec = json.loads(args.spec_json)
    except json.JSONDecodeError as e:
        out = {"ok": False, "errors": [f"invalid spec JSON: {e}"], "warnings": [],
               "needs_fundamentals": False}
    else:
        out = report(spec)

    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    raise SystemExit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
