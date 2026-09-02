"""Print the strategy gallery as JSON (one line to stdout).

Registry discovery must run inside the engine env (it imports pandas, yaml, the
strategy plugins and specs), so the platform shells into this instead of trying
to import backtests/ from its own venv.

Output: JSON list of {name, kind, market, description, params}, where kind is
"spec" for YAML-driven RankAndHold/FixedWeight strategies and "code" for
StrategyBase subclasses. params is each strategy's parameter specs (cheap — no
data load).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.core import markets
from backtests.core.strategy_registry import get_registry
from backtests.core.config_strategy import RankAndHold, FixedWeight


def _param(spec) -> dict:
    return {
        "name": spec.name,
        "label": spec.label,
        "type": spec.param_type,
        "default": spec.default,
        "min": spec.min_value,
        "max": spec.max_value,
        "step": spec.step,
        "choices": spec.choices,
    }


def main() -> None:
    reg = get_registry()
    out = []
    for name in sorted(reg.names()):
        strat = reg.get(name)
        kind = "spec" if isinstance(strat, (RankAndHold, FixedWeight)) else "code"
        try:
            params = [_param(s) for s in strat.get_parameter_specs()]
        except Exception:
            params = []
        out.append({
            "name": name,
            "kind": kind,
            # Which plane the strategy is authored for. Spec-driven ones carry
            # it; the hand-written plugins predate the market registry and are
            # all B3, which is what the default means here.
            "market": getattr(strat, "market", None) or markets.DEFAULT,
            "description": " ".join(str(strat.description).split()),
            "needs_fundamentals": bool(getattr(strat, "needs_fundamentals", False)),
            "params": params,
        })
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
