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
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
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


class Warm:
    """Which zygote may run a call: the interpreter prefix of its argv and the
    third-party modules that interpreter should hold preloaded."""

    def __init__(self, interp: list[str], preload: tuple[str, ...]) -> None:
        self.interp = list(interp)
        self.preload = tuple(preload)
        self.key = (tuple(self.interp), self.preload)


_ZYGOTE_SCRIPT = Path(__file__).with_name("zygote_server.py")
# Off until the API turns it on (main.py's lifespan). The worker and the test
# suite keep plain subprocesses: a backtest is tens of seconds and gains nothing
# from saving half of one, and tests monkeypatch subprocess-level behaviour.
_zygotes_enabled = False
_zygotes: dict[tuple, "Zygote"] = {}
_zygotes_lock = threading.Lock()


class Zygote:
    """A long-lived interpreter that forks a fresh child per call
    (zygote_server.py). Thread-safe: every call is its own connection."""

    def __init__(self, warm: Warm) -> None:
        self.warm = warm
        self._dir = tempfile.mkdtemp(prefix="b3zyg-")  # 0700: only we can connect
        self.path = os.path.join(self._dir, "s")
        self.proc = subprocess.Popen(
            [*warm.interp, str(_ZYGOTE_SCRIPT), self.path, ",".join(warm.preload)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONWARNINGS": "ignore", "PYTHONIOENCODING": "utf-8"},
            cwd=self._dir,
        )
        # Bounded: an interpreter that never comes up must not hang a request.
        ready, _, _ = select.select([self.proc.stdout], [], [], 120)
        if not ready or self.proc.stdout.readline().strip() != b"ready":
            self.close()
            raise RuntimeError("zygote did not start")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def run(self, argv: list[str], *, cwd: Path, env: dict, timeout_s: int,
            stdin: str | None, label: str) -> subprocess.CompletedProcess:
        req = json.dumps({"argv": argv[len(self.warm.interp):], "cwd": str(cwd),
                          "env": env, "stdin": stdin, "timeout": timeout_s})
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            # The supervisor enforces timeout_s; this only covers a zygote that
            # died mid-call without closing the socket.
            s.settimeout(timeout_s + 60)
            s.connect(self.path)
            s.sendall(req.encode())
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while chunk := s.recv(1 << 16):
                chunks.append(chunk)
        res = json.loads(b"".join(chunks))   # empty → ValueError → caller falls back
        if res["timed_out"]:
            return subprocess.CompletedProcess(
                argv, TIMEOUT_EXIT, "", f"{label} timed out after {timeout_s}s")
        return subprocess.CompletedProcess(argv, res["rc"], res["stdout"], res["stderr"])

    def close(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(5)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self._dir, ignore_errors=True)


def enable_zygotes(*warms: Warm) -> None:
    """Turn the warm path on for this process, and start ``warms`` now so the
    first request does not pay the preload. Blocking — call via to_thread."""
    global _zygotes_enabled
    for w in warms:
        try:
            _zygote(w)
        except Exception:  # noqa: BLE001 — cold subprocesses still work
            log.warning("zygote for %s did not start; using plain subprocesses", w.interp)
    # Only now: a request that arrives while the interpreters are importing
    # runs cold instead of queueing behind the spawn lock.
    _zygotes_enabled = True


def disable_zygotes() -> None:
    global _zygotes_enabled
    _zygotes_enabled = False
    with _zygotes_lock:
        for z in _zygotes.values():
            z.close()
        _zygotes.clear()


def _zygote(warm: Warm) -> Zygote:
    with _zygotes_lock:
        z = _zygotes.get(warm.key)
        if z is None or not z.alive():
            if z is not None:
                z.close()
            z = _zygotes[warm.key] = Zygote(warm)
        return z


def _run_warm(argv, warm: Warm, *, cwd, env, timeout_s, stdin, label):
    """The call through a zygote, or None when no zygote could take it (then
    the caller runs it cold — the warm path is an optimisation, never a new
    way to fail)."""
    if argv[:len(warm.interp)] != warm.interp:
        return None
    for _attempt in range(2):           # one respawn if the zygote had died
        try:
            return _zygote(warm).run(argv, cwd=cwd, env=env, timeout_s=timeout_s,
                                     stdin=stdin, label=label)
        except (OSError, ValueError, RuntimeError, KeyError):
            with _zygotes_lock:
                z = _zygotes.pop(warm.key, None)
            if z is not None:
                z.close()
    log.warning("zygote unavailable for %s; running cold", warm.interp)
    return None


def run(
    argv: list[str], *, cwd: Path, env: dict, timeout_s: int, label: str,
    stdin: str | None = None, warm: Warm | None = None,
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

    ``warm`` lets the call run in a forked child of an interpreter that already
    imported the heavy deps (see Zygote) once the process has enabled zygotes;
    the result has the same shape either way.
    """
    proc = None
    if warm is not None and _zygotes_enabled:
        proc = _run_warm(argv, warm, cwd=cwd, env=env, timeout_s=timeout_s,
                         stdin=stdin, label=label)
    try:
        if proc is None:
            proc = subprocess.run(
                argv, cwd=cwd, env=env, capture_output=True, text=True,
                timeout=timeout_s, input=stdin,
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
