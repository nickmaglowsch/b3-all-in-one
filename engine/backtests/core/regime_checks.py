"""A rank_and_hold spec's `regime` overlay, checked before it is saved.

Split out of scripts/validate_spec.py, which stays the one entry point: what a
regime input may read (regimes.regime_flags — one value per date, on this
market), that a numeric flag carries `gt`, that every rule conditions only on
declared inputs, and that a rule's `exposure` is a fraction. Each of these is a
spec that otherwise saves and then parks in CDI for the whole run, silently.
"""
from __future__ import annotations

from backtests.core import markets, regimes
from backtests.core.regimes import REGIME_FLAGS, regime_flags

# What a regime rule may say besides naming its inputs (config_strategy._RULE_ACTIONS,
# plus the N-of-M vote's `min_true`).
_RULE_KEYS = {"top_pct", "exposure", "park", "default", "min_true"}


def _market_regime_flags(market) -> tuple[str, ...]:
    """The regime flags `market` offers; the base list when the market is unknown
    (validate() reports that on its own)."""
    try:
        return regime_flags(markets.get(market))
    except ValueError:
        return REGIME_FLAGS


def _check_regime_flag(name: str, cfg, errors: list[str], flags=REGIME_FLAGS) -> None:
    """A regime input's `flag` must be one of the market series the overlay reads
    by date. A per-ticker frame like above_ma200 has no one value per date: it
    read False on every rebalance and the book parked in CDI for the whole run."""
    flag = cfg.get("flag") if isinstance(cfg, dict) else None
    if not isinstance(flag, str):
        errors.append(f"regime input {name!r}: needs a `flag` naming a shared_data "
                      "series (e.g. bench_above)")
    elif flag not in flags:
        errors.append(f"regime input {name!r}: {flag!r} is not a regime flag — a regime "
                      "reads one market-wide value per date, so a per-ticker frame "
                      "(above_ma200, f_*, …) belongs in the signal instead; regime flags: "
                      f"{', '.join(flags)}")
    elif flag in regimes.NUMERIC_FLAGS and "gt" not in cfg:
        # Read bare, a number is truthy whenever it is not 0: "below its high at
        # all", which is every day but a new high — never the zone meant.
        errors.append(f"regime input {name!r}: {flag} is a number — give it a "
                      "threshold with `gt` (e.g. gt: 0.25)")


def _validate_regime(regime, errors: list[str], flags=REGIME_FLAGS) -> None:
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
        _check_regime_flag(name, cfg, errors, flags)
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
