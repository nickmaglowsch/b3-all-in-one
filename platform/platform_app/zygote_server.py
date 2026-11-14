"""A warm interpreter that forks a fresh child per script run.

Run by the *engine's* and *taxcore's* interpreters, never imported by the
platform: stdlib only, no platform_app imports. subprocs.Zygote spawns it.

Why: every taxcore/engine call from the API was a cold `python script.py`, and
before such a script does any work it imports pandas (+numpy, psycopg, yaml,
openpyxl) — ~0.5 s on a fast core, about twice that on the VPS's. That import
was most of the latency of /tlh/plans, /irpf/darf, the tax-summary refresh,
the strategy save's validation and every --weights valuation.

So this process imports the heavy, *third-party* modules once and then, per
request, forks: the child inherits them already loaded and runs the script
exactly as `python <script> args` / `python -m <module> args` would — its own
argv, env, cwd, stdin and sys.path. Project modules (backtests, b3_pipeline,
taxcore's scripts) are deliberately NOT preloaded: some read the environment at
import time, and each request carries its own env (ENGINE_USER_SPECS_DIR, a
workspace's PYTHONPATH). Importing them in the child keeps that per-request.

Isolation is the same as a subprocess: a child's memory is copy-on-write, a
crash or an OOM kill takes the child and nothing else, and the zygote never
runs request code itself.

Per connection (one request), a supervisor child is forked; it forks the runner
and waits for it with the deadline, so the reported status is exact: the exit
code, a negative signal number for a signal death, or a timeout.

Protocol (unix socket, one JSON document each way, then close):
  request  {"argv": [...after the interpreter...], "cwd", "env", "stdin", "timeout"}
  response {"rc": int | null, "timed_out": bool, "stdout": str, "stderr": str}
"""
from __future__ import annotations

import importlib
import json
import os
import select
import signal
import socket
import sys
import tempfile
import time
import traceback


def _recv_all(conn: socket.socket) -> bytes:
    chunks = []
    while True:
        b = conn.recv(1 << 16)
        if not b:
            return b"".join(chunks)
        chunks.append(b)


def _setup(req: dict, fds: tuple[int, int, int], base_path: list[str]) -> list[str]:
    """Make this process look like a fresh `python <argv>`; returns the argv."""
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    for target, fd in zip((0, 1, 2), fds):
        os.dup2(fd, target)
    os.chdir(req["cwd"])
    os.environ.clear()
    os.environ.update(req["env"])
    if os.environ.get("PYTHONWARNINGS") == "ignore":
        import warnings
        warnings.simplefilter("ignore")

    argv = list(req["argv"])
    first = os.getcwd() if argv[0] == "-m" else os.path.dirname(os.path.abspath(argv[0]))
    pythonpath = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    sys.path[:] = [first, *pythonpath, *base_path]
    importlib.invalidate_caches()
    # site imports the first sitecustomize on the path at startup; a
    # PYTHONPATH one (taxcore's offline network guard) has to be honoured.
    hooks = [h for h in (os.path.join(p, "sitecustomize.py") for p in pythonpath)
             if os.path.exists(h)]
    if hooks:
        import runpy
        runpy.run_path(hooks[0], run_name="sitecustomize")
    return argv


def _exit_code(e: SystemExit) -> int:
    if e.code is None:
        return 0
    if isinstance(e.code, int):
        return e.code
    print(e.code, file=sys.stderr)
    return 1


def _execute(argv: list[str]) -> int:
    """Run the script or module as __main__; its exit status."""
    import runpy
    try:
        if argv[0] == "-m":
            sys.argv = [argv[1], *argv[2:]]
            runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
        else:
            sys.argv = argv
            runpy.run_path(argv[0], run_name="__main__")
        return 0
    except SystemExit as e:
        return _exit_code(e)
    except BaseException:  # noqa: BLE001 — a script's crash is its exit status
        traceback.print_exc()
        return 1


def _run(req: dict, fds: tuple[int, int, int], base_path: list[str]) -> None:
    """In the runner: become `python <argv>` and never return."""
    code = 1
    try:
        code = _execute(_setup(req, fds, base_path))
        try:
            import atexit
            atexit._run_exitfuncs()  # what interpreter shutdown would have run
        except BaseException:  # noqa: BLE001
            pass
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(code & 0xFF)  # as the interpreter does: sys.exit(-1) is 255


def _wait(pid: int, timeout: float) -> tuple[int | None, bool]:
    """(status, timed_out) for the runner, killing it at the deadline."""
    deadline = time.monotonic() + timeout
    try:
        pidfd = os.pidfd_open(pid)
    except (AttributeError, OSError):
        pidfd = None
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
            return None, True
        if pidfd is not None:
            select.select([pidfd], [], [], left)
        else:
            time.sleep(min(left, 0.01))
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status), False


def _serve(conn: socket.socket, base_path: list[str]) -> None:
    """In the supervisor: run one request and answer it."""
    req = json.loads(_recv_all(conn))
    files = [tempfile.TemporaryFile() for _ in range(3)]
    files[0].write((req.get("stdin") or "").encode())
    files[0].seek(0)
    pid = os.fork()
    if pid == 0:
        conn.close()
        _run(req, tuple(f.fileno() for f in files), base_path)
    rc, timed_out = _wait(pid, float(req["timeout"]))
    out = []
    for f in files[1:]:
        f.seek(0)
        out.append(f.read().decode("utf-8", errors="replace"))
    conn.sendall(json.dumps({"rc": rc, "timed_out": timed_out,
                             "stdout": out[0], "stderr": out[1]}).encode())
    conn.shutdown(socket.SHUT_WR)


def main() -> None:
    path, preload = sys.argv[1], [m for m in sys.argv[2].split(",") if m]
    # This script's own directory is no part of any request's path.
    here = os.path.dirname(os.path.abspath(__file__))
    base_path = [p for p in sys.path if p and os.path.abspath(p) != here]
    for mod in preload:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001 — a missing optional dep only costs speed
            pass
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # supervisors reap themselves
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(64)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    # The parent holds our stdin open for as long as it lives; EOF there means
    # it is gone (a restart, a crash), and a zygote nobody can reach must not
    # outlive it holding a few hundred MB of pandas.
    while True:
        ready, _, _ = select.select([srv, sys.stdin], [], [])
        if sys.stdin in ready and not os.read(sys.stdin.fileno(), 1):
            os._exit(0)
        if srv not in ready:
            continue
        conn, _ = srv.accept()
        if os.fork() == 0:
            srv.close()
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # it waits on its runner
            try:
                _serve(conn, base_path)
            finally:
                os._exit(0)
        conn.close()


if __name__ == "__main__":
    main()
