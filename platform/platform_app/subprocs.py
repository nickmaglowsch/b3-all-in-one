"""The bit b3run and enginerun were each doing their own copy of.

Both shell out to a sibling project that has its own interpreter and its own
dependency set, and both need the same two behaviours. Keeping one copy means a
fix to either lands in both — the duplicated versions had already drifted in
their timeout messages, which is how a worker log ends up saying something timed
out without saying what.

Nothing here knows about taxcore or the engine; the callers own their paths,
env and timeouts.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Synthetic exit code for a subprocess we killed on the timeout (shell convention).
TIMEOUT_EXIT = 124


def interpreter(explicit: str, root: Path) -> list[str]:
    """The interpreter that owns ``root``'s dependencies.

    Override wins (the container sets it), then a prebuilt venv (a dev box), then
    ``uv run`` — which resolves the deps on the fly, so CI and a fresh checkout
    work without a build step.
    """
    if explicit:
        return [explicit]
    venv = root / ".venv" / "bin" / "python"
    if venv.exists():
        return [str(venv)]
    # ponytail: fallback for CI/fresh checkouts without a prebuilt venv.
    return ["uv", "run", "--directory", str(root), "python"]


def run(
    argv: list[str], *, cwd: Path, env: dict, timeout_s: int, label: str
) -> subprocess.CompletedProcess:
    """``subprocess.run`` that reports a timeout instead of raising it.

    Every call site branches on ``proc.returncode != 0``, so a TimeoutExpired
    escaping to a request handler or a worker job would be an unhandled 500 —
    and an unbounded wait would pin one of the two worker slots indefinitely.
    ``label`` names the thing that wedged, because "timed out" alone does not
    identify which of a half-dozen subprocesses it was.
    """
    try:
        return subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, TIMEOUT_EXIT, stdout="",
            stderr=f"{label} timed out after {timeout_s}s",
        )
