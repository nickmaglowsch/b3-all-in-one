"""Print one config strategy's raw spec as JSON (one line to stdout).

The gallery (list_strategies.py) exposes each strategy's *params*, which loses the
factor/universe/selection needed to clone a built-in into user space. This resolves
a strategy by name through the registry and, if it is a config (spec-kind) strategy
— a RankAndHold/FixedWeight instance carrying ``.spec`` — prints its raw spec dict
verbatim. Code-kind (StrategyBase) strategies have no data spec: prints nothing and
exits non-zero so the platform maps it to a 404. Unknown name → likewise non-zero.

Honors ENGINE_USER_SPECS_DIR (via the registry) so a tenant can also clone its own
spec-kind strategy.

Output: JSON {"name","kind","spec"}, kind ∈ {"rank_and_hold", "fixed_weight"}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.core.strategy_registry import get_registry
from backtests.core.config_strategy import RankAndHold, FixedWeight


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="strategy registry name")
    args = ap.parse_args()

    try:
        strat = get_registry().get(args.name)
    except KeyError:
        raise SystemExit(1)                             # unknown name → platform 404

    if not isinstance(strat, (RankAndHold, FixedWeight)):
        raise SystemExit(1)                             # code-kind: no data spec → 404

    spec = strat.spec
    kind = spec.get("kind") or ("rank_and_hold" if isinstance(strat, RankAndHold) else "fixed_weight")
    json.dump({"name": args.name, "kind": kind, "spec": spec}, sys.stdout)


if __name__ == "__main__":
    main()
