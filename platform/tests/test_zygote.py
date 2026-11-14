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
