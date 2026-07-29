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

from platform_app import subprocs
from platform_app.config import settings

# Host default: platform/platform_app/enginerun.py -> b3-all-in-one/engine.
# Container/override: settings.engine_dir (ENGINE_DIR env) -> /opt/engine.
_DEFAULT_ENGINE = Path(__file__).resolve().parents[2] / "engine"
ENGINE = Path(settings.engine_dir) if settings.engine_dir else _DEFAULT_ENGINE

# Mirror of advise_weights.NEEDS_FUNDAMENTALS_EXIT: the strategy needs CVM
# fundamentals but fundamentals_pit is empty in the active engine DB.
NEEDS_FUNDAMENTALS_EXIT = 3
# Re-exported: callers branch on enginerun.TIMEOUT_EXIT.
TIMEOUT_EXIT = subprocs.TIMEOUT_EXIT


class EngineNeedsFundamentals(RuntimeError):
    """advise_weights signalled that a strategy needs CVM fundamentals which are
    not ingested (exit code 3 + a JSON error line on stderr). Routes map this to
    a 422 with a friendly message naming the strategy."""

    def __init__(self, strategy: str, message: str) -> None:
        self.strategy = strategy
        self.message = message
        super().__init__(message)


def _engine_python() -> list[str]:
    """The interpreter that has the engine's deps (pandas, psycopg, yaml)."""
    return subprocs.interpreter(settings.engine_python, ENGINE)


def _env(extra_env: dict | None = None) -> dict:
    # Forward the parent env (so ENGINE_BENCH_OFFLINE flows through) and pin the
    # market-plane DSN the engine reads. extra_env (e.g. ENGINE_USER_SPECS_DIR)
    # is merged last so callers that name a user strategy can point the registry
    # at the tenant's materialized specs.
    return {
        **os.environ,
        "ENGINE_DATABASE_URL": settings.engine_database_url,
        "PYTHONWARNINGS": "ignore",
        **(extra_env or {}),
    }


def run_script(
    script: str, args: list[str], extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """``python scripts/<script> [args...]`` with the engine interpreter.

    A timeout returns exit code 124 rather than raising, so every existing
    ``if proc.returncode != 0`` caller reports it as an engine failure with no
    new error handling — and no request or job can pin a thread forever.
    """
    return subprocs.run(
        [*_engine_python(), str(ENGINE / "scripts" / script), *args],
        cwd=ENGINE,
        env=_env(extra_env),
        timeout_s=settings.subprocess_timeout_s,
        label=script,
    )


def run_module(
    module_args: list[str], timeout_s: int | None = None
) -> subprocess.CompletedProcess:
    """``python -m <module> [args...]`` with the engine interpreter.

    For the ingestion pipelines (``b3_pipeline.main``, ``b3_pipeline.cvm_main``),
    which are modules rather than scripts/ entry points. They take minutes to
    hours, so they get their own timeout rather than the per-request default —
    but still a bounded one, so a wedged download can't hold a worker slot for a
    week. Timeout maps to exit 124 like the other runners.
    """
    return subprocs.run(
        [*_engine_python(), "-m", *module_args],
        cwd=ENGINE,
        env=_env(),
        timeout_s=timeout_s if timeout_s is not None else settings.ingest_timeout_s,
        label=module_args[0],
    )


def advise_weights(
    *,
    strategy: str | None = None,
    weights: dict | None = None,
    positions: dict | None = None,
    nav_cash: float = 0.0,
    extra_env: dict | None = None,
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
    proc = run_script("advise_weights.py", args, extra_env)
    if proc.returncode == NEEDS_FUNDAMENTALS_EXIT:
        raise _needs_fundamentals(proc.stderr, strategy)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
    return subprocs.last_json(proc.stdout, strict=True)


def _needs_fundamentals(stderr: str, strategy: str | None) -> EngineNeedsFundamentals:
    """Build EngineNeedsFundamentals from advise_weights' JSON error line."""
    payload: dict = {}
    for line in reversed((stderr or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                payload = {}
            break
    return EngineNeedsFundamentals(
        payload.get("strategy") or strategy or "?",
        payload.get("message") or "requires fundamentals data",
    )
