"""Run the backtest engine's headless scripts as subprocesses.

Mirrors b3run (taxcore), but for engine/scripts/*.py. The engine reads the
market plane from Postgres via ENGINE_DATABASE_URL — we pass that into the
subprocess env. We never import backtests/ into the platform venv; registry
discovery and the simulation only run inside the engine's own interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from platform_app.config import settings

# Host default: platform/platform_app/enginerun.py -> b3-all-in-one/engine.
# Container/override: settings.engine_dir (ENGINE_DIR env) -> /opt/engine.
_DEFAULT_ENGINE = Path(__file__).resolve().parents[2] / "engine"
ENGINE = Path(settings.engine_dir) if settings.engine_dir else _DEFAULT_ENGINE


def _engine_python() -> list[str]:
    """The interpreter that has the engine's deps (pandas, psycopg, yaml)."""
    if settings.engine_python:
        return [settings.engine_python]
    venv = ENGINE / ".venv" / "bin" / "python"
    if venv.exists():
        return [str(venv)]
    # ponytail: fallback for CI/fresh checkouts without a prebuilt engine venv.
    return ["uv", "run", "--directory", str(ENGINE), "python"]


def _env() -> dict:
    # Forward the parent env (so ENGINE_BENCH_OFFLINE flows through) and pin the
    # market-plane DSN the engine reads.
    return {
        **os.environ,
        "ENGINE_DATABASE_URL": settings.engine_database_url,
        "PYTHONWARNINGS": "ignore",
    }


def run_script(script: str, args: list[str]) -> subprocess.CompletedProcess:
    """``python scripts/<script> [args...]`` with the engine interpreter."""
    return subprocess.run(
        [*_engine_python(), str(ENGINE / "scripts" / script), *args],
        cwd=ENGINE, env=_env(), capture_output=True, text=True,
    )


def _last_json(text: str) -> dict:
    """Parse the last JSON object line of stdout — build_shared_data prints
    progress lines before advise_weights emits its one-line JSON payload."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"no JSON object in engine output:\n{text[-2000:]}")


def advise_weights(
    *,
    strategy: str | None = None,
    weights: dict | None = None,
    positions: dict | None = None,
    nav_cash: float = 0.0,
) -> dict:
    """Run scripts/advise_weights.py and return its parsed JSON. Pass either a
    ``strategy`` name (signals) or fixed ``weights`` (no signals). When
    ``positions`` is given the payload also carries a trade list + valuations.
    Raises RuntimeError on a non-zero exit (caller maps to HTTP)."""
    args: list[str] = []
    if strategy:
        args.append(strategy)
    if weights is not None:
        args += ["--weights", json.dumps(weights)]
    if positions is not None:
        args += ["--positions", json.dumps(positions), "--nav-cash", str(float(nav_cash))]
    proc = run_script("advise_weights.py", args)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
    return _last_json(proc.stdout)
