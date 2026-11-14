"""subprocs' warm path (Zygote) must be indistinguishable from a cold subprocess.

Every caller branches on returncode/stdout/stderr, so the contract pinned here
is exactly the one subprocs.run documents: exit codes, signal deaths named,
timeouts reported, stdin/env/cwd per call, `-m` with a PYTHONPATH that carries
a sitecustomize (taxcore's offline guard). The interpreter is this one — the
server is stdlib only, so nothing about it needs the engine's venv.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from platform_app import subprocs

PY = sys.executable

SCRIPT = """\
import json, os, sys
mode = sys.argv[1]
if mode == "echo":
    print(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(),
                      "env": os.environ.get("FOO"), "cwd": os.getcwd(),
                      "path0": sys.path[0]}))
elif mode == "exit":
    sys.exit(int(sys.argv[2]))
elif mode == "sleep":
    import time; time.sleep(30)
elif mode == "kill":
    os.kill(os.getpid(), 9)
elif mode == "raise":
    raise ValueError("boom")
"""


@pytest.fixture
def warm(tmp_path):
    (tmp_path / "s.py").write_text(SCRIPT)
    w = subprocs.Warm([PY], ("json",))
    subprocs.enable_zygotes(w)
    yield w
    subprocs.disable_zygotes()


def _run(w, args, tmp_path, *, cwd=None, env=None, stdin=None, timeout_s=30):
    return subprocs.run([PY, *args], cwd=Path(cwd or tmp_path),
                        env={"FOO": "bar", "PATH": os.environ["PATH"], **(env or {})},
                        timeout_s=timeout_s, label="probe", stdin=stdin, warm=w)


def test_the_call_runs_in_a_zygote_and_sees_its_own_argv_stdin_env_and_cwd(warm, tmp_path):
    import json

    assert subprocs._zygotes[warm.key].alive()
    p = _run(warm, [str(tmp_path / "s.py"), "echo", "a b"], tmp_path, stdin="hello")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out == {"argv": ["echo", "a b"], "stdin": "hello", "env": "bar",
                   "cwd": str(tmp_path), "path0": str(tmp_path)}


@pytest.mark.parametrize("code,expected", [("0", 0), ("3", 3), ("-1", 255)])
def test_exit_codes_are_the_interpreters(warm, tmp_path, code, expected):
    assert _run(warm, [str(tmp_path / "s.py"), "exit", code], tmp_path).returncode == expected


def test_an_exception_is_exit_1_with_its_traceback(warm, tmp_path):
    p = _run(warm, [str(tmp_path / "s.py"), "raise"], tmp_path)
    assert p.returncode == 1 and "ValueError: boom" in p.stderr


def test_a_signal_death_is_named_like_a_cold_one(warm, tmp_path):
    p = _run(warm, [str(tmp_path / "s.py"), "kill"], tmp_path)
    assert p.returncode == -9 and "probe was killed by SIGKILL" in p.stderr


def test_a_timeout_is_reported_not_raised(warm, tmp_path):
    p = _run(warm, [str(tmp_path / "s.py"), "sleep"], tmp_path, timeout_s=1)
    assert p.returncode == subprocs.TIMEOUT_EXIT
    assert p.stderr == "probe timed out after 1s"


def test_dash_m_honours_pythonpath_and_its_sitecustomize(warm, tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "mod.py").write_text(
        "import sys, guarded\nprint(sys.argv[1:], guarded.ON)\n")
    (tmp_path / "guarded.py").write_text("ON = False\n")
    (tmp_path / "sitecustomize.py").write_text("import guarded\nguarded.ON = True\n")
    p = _run(warm, ["-m", "pkg.mod", "--x"], tmp_path, env={"PYTHONPATH": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "['--x'] True"


def test_a_dead_zygote_is_respawned(warm, tmp_path):
    first = subprocs._zygotes[warm.key]
    first.proc.kill()
    first.proc.wait()
    assert _run(warm, [str(tmp_path / "s.py"), "exit", "0"], tmp_path).returncode == 0
    assert subprocs._zygotes[warm.key] is not first


def test_without_a_zygote_it_falls_back_to_a_cold_subprocess(tmp_path, monkeypatch):
    (tmp_path / "s.py").write_text(SCRIPT)
    w = subprocs.Warm([PY], ())
    monkeypatch.setattr(subprocs, "_zygotes_enabled", True)

    def no_zygote(_warm):
        raise RuntimeError("zygote did not start")

    monkeypatch.setattr(subprocs, "_zygote", no_zygote)
    p = _run(w, [str(tmp_path / "s.py"), "exit", "4"], tmp_path)
    assert p.returncode == 4


def test_the_zygote_exits_with_its_parent(tmp_path):
    """Our stdin closing is the parent going away; nothing must linger."""
    sock = tmp_path / "z.sock"
    proc = subprocess.Popen(
        [PY, str(subprocs._ZYGOTE_SCRIPT), str(sock), ""],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdout.readline().strip() == b"ready"
    proc.stdin.close()
    assert proc.wait(10) == 0


SCRIPT_MORE = """\
import os, sys, threading, time
mode = sys.argv[1]
if mode == "thread":
    def late():
        time.sleep(0.3)
        print("thread-finished", flush=True)
    threading.Thread(target=late).start()
    print("main-done", flush=True)
elif mode == "interrupt":
    raise KeyboardInterrupt
elif mode == "count":
    with open(sys.argv[2], "a") as fh:
        fh.write("ran\\n")
    time.sleep(30)
"""


def test_non_daemon_threads_finish_before_the_exit(warm, tmp_path):
    (tmp_path / "m.py").write_text(SCRIPT_MORE)
    p = _run(warm, [str(tmp_path / "m.py"), "thread"], tmp_path)
    assert p.returncode == 0
    assert p.stdout.split() == ["main-done", "thread-finished"]


def test_the_interpreters_own_exit_codes(warm, tmp_path):
    (tmp_path / "m.py").write_text(SCRIPT_MORE)
    assert _run(warm, [str(tmp_path / "missing.py")], tmp_path).returncode == 2
    assert _run(warm, [str(tmp_path / "m.py"), "interrupt"], tmp_path).returncode == -2


def test_a_call_the_zygote_lost_is_a_failure_never_a_second_run(warm, tmp_path):
    """Once the request is in, the script may have run: a zygote that dies
    mid-call is answered as a killed child, not retried warm or cold."""
    import threading
    import time

    (tmp_path / "m.py").write_text(SCRIPT_MORE)
    log = tmp_path / "ran.log"
    z = subprocs._zygotes[warm.key]

    def kill_soon():
        while not log.exists():
            time.sleep(0.01)
        import os
        import signal as sig
        for pid in _children(z.proc.pid):     # the call's supervisor and runner
            os.kill(pid, sig.SIGKILL)

    threading.Thread(target=kill_soon, daemon=True).start()
    p = _run(warm, [str(tmp_path / "m.py"), "count", str(log)], tmp_path)
    assert p.returncode < 0
    assert log.read_text() == "ran\n"


def _children(pid: int) -> list[int]:
    """Every descendant of ``pid`` (the supervisors and runners)."""
    import subprocess as sp
    out = sp.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    kids = [int(k) for k in out]
    return kids + [g for k in kids for g in _children(k)]


def test_a_zygote_that_never_says_ready_leaves_nothing_behind(tmp_path, monkeypatch):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys, time\nprint('nope', flush=True)\ntime.sleep(60)\n")
    monkeypatch.setattr(subprocs, "_ZYGOTE_SCRIPT", bad)
    with pytest.raises(subprocs.Unreachable):
        subprocs.Zygote(subprocs.Warm([PY], ()))
    import subprocess as sp
    assert not sp.run(["pgrep", "-f", str(bad)], capture_output=True).stdout


def test_a_zygote_starts_with_fds_past_select_s_limit(tmp_path):
    """select() refuses fds >= 1024; an API with many sockets open got there."""
    import os
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < 1200:
        pytest.skip("fd limit too low to reach 1024")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 1200), hard))
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(1100)]
    try:
        z = subprocs.Zygote(subprocs.Warm([PY], ()))
        assert z.alive()
        z.close()
    finally:
        for fd in held:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_a_failure_never_evicts_the_zygote_another_thread_put_back():
    w = subprocs.Warm([PY], ())

    class Fake:
        def close(self):
            pass

    healthy, dead = Fake(), Fake()
    subprocs._zygotes[w.key] = healthy
    try:
        subprocs._drop(w, dead)
        assert subprocs._zygotes[w.key] is healthy
    finally:
        subprocs._zygotes.pop(w.key, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_gather_all_waits_for_every_sibling_then_raises_the_failure():
    import asyncio

    finished = []

    async def slow():
        await asyncio.sleep(0.1)
        finished.append("slow")
        return 1

    async def boom():
        raise ValueError("first")

    with pytest.raises(ValueError, match="first"):
        await subprocs.gather_all(boom(), slow())
    assert finished == ["slow"]
    assert await subprocs.gather_all(slow(), slow()) == [1, 1]
