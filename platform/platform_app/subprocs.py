"""The bit b3run and enginerun were each doing their own copy of.

Both shell out to a sibling project that has its own interpreter and its own
dependency set, and both need the same two behaviours. Three near-identical
copies of the same try/except and the same venv-or-uv ladder is three places to
fix a bug in it, and three chances for one of them to be missed.

Nothing here knows about taxcore or the engine; the callers own their paths,
env and timeouts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import subprocess
import uuid
import weakref
from pathlib import Path

log = logging.getLogger(__name__)

# Synthetic exit code for a subprocess we killed on the timeout (shell convention).
TIMEOUT_EXIT = 124
# ...and for one that never started (E2BIG, ENOENT, EMFILE): the shell's
# "found but could not execute".
EXEC_FAILED_EXIT = 126
# How many subprocesses the API process waits on at once. See offload().
API_SLOTS = 4


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
    argv: list[str], *, cwd: Path, env: dict, timeout_s: int, label: str,
    stdin: str | None = None,
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

    ``stdin`` is for payloads that grow with the tenant's data (a backtest's
    params, a what-if's dated flows). As one argv element they hit the kernel's
    128 KB MAX_ARG_STRLEN and the exec itself fails with E2BIG. An exec that
    fails for any reason is reported like a failed child too, for the same
    reason as the timeout: callers branch on returncode, never on OSError.
    """
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s,
            input=stdin,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, TIMEOUT_EXIT, stdout="",
            stderr=f"{label} timed out after {timeout_s}s",
        )
    except OSError as e:
        return subprocess.CompletedProcess(
            argv, EXEC_FAILED_EXIT, stdout="", stderr=f"{label} could not start: {e}")
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


_slots: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def offload(fn, /, *args, **kwargs):
    """``asyncio.to_thread(fn, ...)`` behind an API-wide cap of API_SLOTS.

    Every route that shells into taxcore or the engine used to go straight to
    the default executor, which is ~cpu+4 threads shared with every other
    to_thread in the process (uploads, workspace builds). Fifteen parallel IRPF
    reports from one tenant filled it for up to subprocess_timeout_s, and every
    other request queued behind them. The cap keeps threads free for the rest.

    One semaphore per event loop rather than one module global: an asyncio
    primitive binds to the loop it first waits on, and a process that runs more
    than one loop (the test suite does) would otherwise trip over a stale one.
    """
    loop = asyncio.get_running_loop()
    sem = _slots.get(loop)
    if sem is None:
        sem = _slots[loop] = asyncio.Semaphore(API_SLOTS)
    async with sem:
        return await asyncio.to_thread(fn, *args, **kwargs)


def report(label: str, output: str | subprocess.CompletedProcess) -> str:
    """Log a failed subprocess's output tail; return what the tenant may see.

    The tail is a traceback: absolute paths of the deploy, sometimes a DSN host,
    once in a while a line of someone's book. privacy.py already strips it from
    exports for that reason, and a 500 detail is no different. The ref ties the
    two together when someone asks what went wrong.
    """
    if isinstance(output, subprocess.CompletedProcess):
        output = output.stderr or output.stdout or ""
    ref = uuid.uuid4().hex[:8]
    log.error("%s failed (ref %s):\n%s", label, ref, (output or "")[-2000:])
    return f"{label} falhou (ref {ref})"


# A directory prefix: one or more /segment/ runs not preceded by a word char,
# so "a/b" in prose and URLs' "//" are left alone, and only the basename stays.
_DIR_PREFIX = re.compile(r"(?<![\w.:/])(?:/[^\s/'\"]+)+/")


def redact(text: str | None) -> str | None:
    """A stored subprocess error, fit to hand back to the tenant who caused it.

    Import and backtest errors are worth showing — "this file is not a B3
    extract" is the user's to fix — but the paths around them map the server.
    Keeps every basename, drops every directory.
    """
    return _DIR_PREFIX.sub("", text) if text else text
