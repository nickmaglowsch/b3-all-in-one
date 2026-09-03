"""The bit b3run and enginerun were each doing their own copy of.

Both shell out to a sibling project that has its own interpreter and its own
dependency set, and both need the same two behaviours. Three near-identical
copies of the same try/except and the same venv-or-uv ladder is three places to
fix a bug in it, and three chances for one of them to be missed.

Nothing here knows about taxcore or the engine; the callers own their paths,
env and timeouts.
"""
from __future__ import annotations

import json
import signal
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

    A child killed by a signal gets the same treatment for the same reason. Now
    that the compose files cap container memory, the engine exceeding its share
    is a routine, expected failure — and it arrives as returncode -9 with an
    empty stderr, which every caller stores verbatim. The tenant then sees a run
    that failed with no reason given. Say which signal, and name the likeliest
    cause of the one signal that has one.
    """
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, TIMEOUT_EXIT, stdout="",
            stderr=f"{label} timed out after {timeout_s}s",
        )
    if proc.returncode < 0:
        proc.stderr = _killed(label, -proc.returncode, proc.stderr)
    return proc


def _killed(label: str, signum: int, stderr: str) -> str:
    """Explain a signal death, keeping whatever the child managed to say first."""
    try:
        name = signal.Signals(signum).name
    except ValueError:                      # a signal this platform has no name for
        name = f"signal {signum}"
    why = " (out of memory, most likely the container's mem_limit)" if (
        signum == signal.SIGKILL) else ""
    return f"{label} was killed by {name}{why}\n{stderr}".rstrip()


def last_json(text: str, *, strict: bool = False) -> dict:
    """The JSON object a subprocess printed on stdout, as a dict.

    Scans backwards for a line that opens an object and decodes from there, so
    one reader covers both conventions in this repo: the engine's single-line
    payload preceded by progress lines, and load_b3's pretty-printed multi-line
    one. raw_decode means output *after* the object is fine too.

    ``strict`` picks the error contract. Default returns ``{}`` — a caller that
    writes an Import row from the payload would rather record nothing than 500.
    strict=True raises, for callers answering the request with this dict.
    """
    offset = len(text)
    for line in reversed(text.splitlines(keepends=True)):
        offset -= len(line)
        if not line.lstrip().startswith("{"):
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[offset:].lstrip())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    if strict:
        raise ValueError(f"no JSON object in subprocess output:\n{text[-2000:]}")
    return {}
