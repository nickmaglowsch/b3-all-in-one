"""Print the named-signal catalog as JSON (one line to stdout).

The rule builder's factor picker needs the named signals from
backtests/strategies/signals.yaml. Mirrors list_strategies.py: the platform
shells this instead of importing backtests/ from its own venv.

Output: JSON list of {name, expr, ...default params} per named signal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.core.config_strategy import _signal_defs


def main() -> None:
    out = [{"name": name, **defn} for name, defn in _signal_defs().items()]
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
