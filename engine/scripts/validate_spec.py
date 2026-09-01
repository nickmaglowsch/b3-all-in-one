"""Validate a user-authored strategy spec — no market-data run.

Usage:
    python scripts/validate_spec.py --spec-json '<json>'

<json> is an engine spec dict (the same schema strategy_registry.discover()
loads from specs/*.yaml). Constructs the matching config engine (RankAndHold /
FixedWeight), runs get_parameter_specs(), and — for a rank_and_hold signal —
validates the factor: check_syntax on an inline expr:, resolves a factor:
reference against signals.yaml or the whitelisted shared_data keys, rejects
store:<id>, and flags needs_fundamentals when any f_* key is referenced.

Prints one JSON line {"ok": bool, "errors": [...], "needs_fundamentals": bool}
to stdout, exit 0 when ok else 1. The platform shells this on save.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.core import markets, signal_dsl
from backtests.core.config_strategy import RankAndHold, FixedWeight, _signal_defs

# shared_data frames a bare `factor:` reference may name, per
# docs/config_strategies.md "Primitive vocabulary". Fundamentals `f_*` keys are
# open-ended (matched by prefix) and imply needs_fundamentals.
SHARED_DATA_KEYS = {
    "ret", "log_ret", "adj_close", "raw_close", "adtv", "close_px",
    "fin_vol", "vol_60d", "vol_20d", "atr_m", "mf_composite",
    # Market-neutral aliases for the benchmark frames; see shared_data.
    "bench_ret", "bench_px",
}

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


def _validate_rank_signal(spec: dict, errors: list[str]) -> bool:
    """Validate a rank_and_hold spec's signal block, appending any errors.
    Returns whether the signal references CVM fundamentals (f_* keys)."""
    sig = spec.get("signal") or {}

    if "expr" in sig:
        expr = sig["expr"]
        try:
            signal_dsl.check_syntax(expr)
        except ValueError as e:
            errors.append(str(e))
        return any(n.startswith("f_") for n in _expr_names(expr))

    ref = sig.get("factor")
    if isinstance(ref, str) and ref.startswith("store:"):
        errors.append(f"factor {ref!r}: store: references are not allowed for user strategies")
        return False
    if ref in _signal_defs():
        return False
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

    return {"ok": not errors, "errors": errors, "needs_fundamentals": needs_funds}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec-json", required=True, help="engine spec dict as JSON")
    args = ap.parse_args()

    try:
        spec = json.loads(args.spec_json)
    except json.JSONDecodeError as e:
        report = {"ok": False, "errors": [f"invalid spec JSON: {e}"], "needs_fundamentals": False}
    else:
        report = validate(spec)

    json.dump(report, sys.stdout)
    sys.stdout.write("\n")
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
