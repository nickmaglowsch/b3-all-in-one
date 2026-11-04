"""Validate a user-authored strategy spec — no backtest run.

Usage:
    python scripts/validate_spec.py --spec-json '<json>'
    python scripts/validate_spec.py --spec-json - < spec.json   # how the platform calls it

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

from backtests.core import dividends, markets, regimes, signal_dsl, sleeve_tickers
from backtests.core.sectors import CVM_SECTORS
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
    # Each name's drawdown from its own all-time high (shared_data, full history).
    "ath_drawdown",
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
    if flags := sorted(_expr_names(expr) & regimes.REGIME_ONLY):
        errors.append(f"{flags}: a regime flag (one number per date), not data an "
                      "expression can read — per name, use ath_drawdown")
    tree = ast.parse(expr, mode="eval")
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    if unknown := sorted(called - signal_dsl.FUNCTION_NAMES):
        errors.append(f"signal expr: unknown function(s) {unknown}; available: "
                      f"{sorted(signal_dsl.FUNCTION_NAMES)}")
    return any(n.startswith("f_") for n in _expr_names(expr))


# What a regime rule may say besides naming its inputs (config_strategy._eval_regime).
_RULE_KEYS = {"top_pct", "exposure", "park", "default", "min_true"}




def _validate_sector_keywords(spec: dict, errors: list[str]) -> None:
    """A universe.sectors keyword must match some CVM sector: one that matches
    none (an English or FCA-era name) filters every name in or out silently."""
    sectors = (spec.get("universe") or {}).get("sectors") or {}
    lower = [s.lower() for s in CVM_SECTORS]
    for key in ("include", "exclude"):
        for kw in sectors.get(key) or []:
            if isinstance(kw, str) and not any(kw.lower() in s for s in lower):
                errors.append(f"universe.sectors.{key}: unknown CVM sector {kw!r} (a keyword "
                              f"matches as a substring); CVM's sectors: {', '.join(sorted(CVM_SECTORS))}")


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
        elif cfg["flag"] in SHARED_DATA_KEYS - {"bench_ret", "bench_px"}:
            errors.append(f"regime input {name!r}: {cfg['flag']} is a per-name frame, not "
                          "a regime flag (it would never read true) — use it in an "
                          "expression such as exposure_expr")
        elif cfg["flag"] in regimes.NUMERIC_FLAGS and "gt" not in cfg:
            # Read bare, a number is truthy whenever it is not 0: "below its high
            # at all", which is every day but a new high — never the zone meant.
            errors.append(f"regime input {name!r}: {cfg['flag']} is a number — give "
                          "it a threshold with `gt` (e.g. gt: 0.25)")
    for rule in regime["rules"]:
        extra = set(rule) - set(inputs) - _RULE_KEYS if isinstance(rule, dict) else {"?"}
        if extra:
            errors.append(f"regime rule {rule!r}: {sorted(extra)} is not a declared input")
        elif "exposure" in rule:
            _validate_exposure(rule, errors)


def _validate_exposure(rule: dict, errors: list[str]) -> None:
    """`exposure` is the fraction of the book a rule invests, the rest in CDI. A
    bool is refused (True would read as 1.0, all in, which is no knob at all), and
    so is one beside `park`, which parks whatever the exposure says."""
    x = rule["exposure"]
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0.0 <= x <= 1.0:
        errors.append(f"regime rule {rule!r}: exposure must be a number in [0, 1] "
                      "(the fraction invested; the rest sits in CDI)")
    elif rule.get("park"):
        errors.append(f"regime rule {rule!r}: exposure and park contradict each other "
                      "— park is exposure 0")


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
    funds = _validate_vars(local, sig, errors)
    if "legs" in sig:
        funds |= _validate_legs(sig, errors, local)
    else:
        funds |= _validate_factor(sig, errors, local)
    if spec.get("weighting") == "expr":
        if isinstance(spec.get("weight_expr"), str):
            funds |= _check_expr(spec["weight_expr"], errors)
            _reads_data(spec["weight_expr"], "weight_expr", sig, local, errors)
        else:
            errors.append("weighting `expr` needs a `weight_expr` expression")
    if "exposure_expr" in spec:
        x = spec["exposure_expr"]
        if isinstance(x, str) and x.strip():
            funds |= _check_expr(x, errors)
            _reads_data(x, "exposure_expr", sig, local, errors)
        else:
            errors.append("exposure_expr must be an expression string")
    return funds


# A var may not reuse a name something else already answers to: inside an
# expression the scalars win and as a `factor:` the named signals do, so a var
# called `lookback` or `momentum` would silently be ignored.
_DATA_PREFIXES = ("f_", "div_", "bench_", "ibov_", "cdi_", "sector_")


def _var_name_taken(name: str, sig: dict) -> bool:
    return (not name.isidentifier() or name in signal_dsl.FUNCTION_NAMES
            or name in _signal_defs() or name in SHARED_DATA_KEYS
            or name in sig or name in ("lookback", "weight", "transform", "has_glitch")
            or name.startswith(_DATA_PREFIXES))


def _reads_data(expr: str, what: str, sig: dict, local: dict, errors: list[str]) -> None:
    """A leg or weight_expr must name some market data (or a var); one built of
    numbers and scalar keys alone is a constant, not a cross-section."""
    scalars = {k for k, v in sig.items() if isinstance(v, (int, float))} | {"lookback"}
    if not _expr_names(expr) - scalars:
        errors.append(f"{what}: {expr!r} reads no market data")


def _validate_vars(local: dict, sig: dict, errors: list[str]) -> bool:
    funds = False
    for name, expr in local.items():
        if _var_name_taken(name, sig):
            errors.append(f"signal.vars: {name!r} is taken (a function, a named "
                          "signal, a data frame or a signal key) — pick another name")
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
            if isinstance(leg.get("expr"), str):
                _reads_data(leg["expr"], f"signal.legs[{i}]", sig, local, errors)
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
            _validate_sector_keywords(spec, errors)
            sleeve_tickers.check_universe_shape(spec, errors)
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
    exprs += [spec[k] for k in ("weight_expr", "exposure_expr") if isinstance(spec.get(k), str)]
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
    elif out["ok"] and (spec.get("universe") or {}).get("tickers"):
        out["errors"] += sleeve_tickers.check_universe(spec)
        out["ok"] = not out["errors"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec-json", required=True,
                    help='engine spec dict as JSON, or "-" to read it from stdin')
    args = ap.parse_args()
    raw = sys.stdin.read() if args.spec_json == "-" else args.spec_json

    try:
        spec = json.loads(raw)
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
