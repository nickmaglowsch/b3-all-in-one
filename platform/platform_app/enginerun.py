"""Run the backtest engine's headless scripts as subprocesses.

Mirrors b3run (taxcore), but for engine/scripts/*.py. The engine reads the
market plane from Postgres via ENGINE_DATABASE_URL — we pass that into the
subprocess env. We never import backtests/ into the platform venv; registry
discovery and the simulation only run inside the engine's own interpreter.
"""
from __future__ import annotations

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
