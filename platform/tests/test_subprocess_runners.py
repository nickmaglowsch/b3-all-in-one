"""Shared contract for the two subprocess runners (b3run, enginerun).

Both shell out to a sibling project's interpreter, and both promise the same two
things to every caller:

  * a timeout is reported as exit code 124, never raised — every call site
    branches on ``proc.returncode != 0`` and would otherwise leak a
    TimeoutExpired up through a request handler or a worker job;
  * the interpreter is resolved settings-override → prebuilt venv → ``uv run``,
    so a container (which sets the override), a dev box (which has the venv) and
    a fresh checkout (which has neither) all work.

The contract is what matters, not which module implements it — these tests are
written against both runners at once so the shared helper they now delegate to
cannot drift back apart.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from platform_app import b3run, enginerun
from platform_app.config import settings

# Both runners invoke `python -m <module>`, so the fixtures below drop a real
# module into the directory the runner puts on the path and name it here.
SLEEPER = ["sleeper"]


def _write_sleeper(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "sleeper.py").write_text("import time; time.sleep(30)")


@pytest.fixture
def instant_timeout(monkeypatch):
    """One second, so the timeout path is exercised without a slow test."""
    monkeypatch.setattr(settings, "subprocess_timeout_s", 1)
    monkeypatch.setattr(settings, "ingest_timeout_s", 1)


@pytest.fixture
def this_interpreter(monkeypatch):
    """Point both runners at the running interpreter — no venv build needed."""
    monkeypatch.setattr(settings, "taxcore_python", sys.executable)
    monkeypatch.setattr(settings, "engine_python", sys.executable)


def test_b3run_module_timeout_is_exit_124_not_an_exception(
    tmp_path, instant_timeout, this_interpreter
):
    _write_sleeper(tmp_path)
    proc = b3run.run_module(tmp_path, SLEEPER)
    assert proc.returncode == b3run.TIMEOUT_EXIT == 124
    assert "timed out" in proc.stderr
    assert proc.stdout == ""


def test_enginerun_module_timeout_is_exit_124_not_an_exception(
    monkeypatch, tmp_path, instant_timeout, this_interpreter
):
    _write_sleeper(tmp_path)  # cwd=ENGINE, and `-m` resolves from cwd
    monkeypatch.setattr(enginerun, "ENGINE", tmp_path)
    proc = enginerun.run_module(SLEEPER, timeout_s=1)
    assert proc.returncode == enginerun.TIMEOUT_EXIT == 124
    assert "timed out" in proc.stderr


def test_enginerun_script_timeout_is_exit_124_not_an_exception(
    monkeypatch, tmp_path, instant_timeout, this_interpreter
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "slow.py").write_text("import time; time.sleep(30)")
    monkeypatch.setattr(enginerun, "ENGINE", tmp_path)

    proc = enginerun.run_script("slow.py", [])
    assert proc.returncode == 124
    assert "slow.py" in proc.stderr and "timed out" in proc.stderr


def test_the_timeout_message_names_what_timed_out(
    tmp_path, instant_timeout, this_interpreter
):
    """A bare 'timed out' in a worker log does not say which of the half-dozen
    subprocesses wedged."""
    _write_sleeper(tmp_path)
    slow = b3run.run_module(tmp_path, SLEEPER)
    assert SLEEPER[0] in slow.stderr


def test_a_normal_exit_is_passed_through_untouched(tmp_path, this_interpreter):
    """The timeout wrapper must not disturb the ordinary path: real exit code,
    real stdout, real stderr."""
    (tmp_path / "quick.py").write_text("import sys; print('hi'); sys.exit(7)")
    proc = b3run.run_module(tmp_path, ["quick"])
    assert proc.returncode == 7
    assert proc.stdout.strip() == "hi"


@pytest.mark.parametrize(
    "resolver, dir_attr, setting",
    [
        (b3run._taxcore_python, "TAXCORE", "taxcore_python"),
        (enginerun._engine_python, "ENGINE", "engine_python"),
    ],
)
def test_explicit_interpreter_setting_wins(monkeypatch, resolver, dir_attr, setting):
    monkeypatch.setattr(settings, setting, "/custom/python")
    assert resolver() == ["/custom/python"]


@pytest.mark.parametrize(
    "module, resolver, dir_attr, setting",
    [
        (b3run, b3run._taxcore_python, "TAXCORE", "taxcore_python"),
        (enginerun, enginerun._engine_python, "ENGINE", "engine_python"),
    ],
)
def test_prebuilt_venv_beats_uv_run(
    monkeypatch, tmp_path, module, resolver, dir_attr, setting
):
    monkeypatch.setattr(settings, setting, "")
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    monkeypatch.setattr(module, dir_attr, tmp_path)
    assert resolver() == [str(venv_python)]


@pytest.mark.parametrize(
    "module, resolver, dir_attr, setting",
    [
        (b3run, b3run._taxcore_python, "TAXCORE", "taxcore_python"),
        (enginerun, enginerun._engine_python, "ENGINE", "engine_python"),
    ],
)
def test_fresh_checkout_falls_back_to_uv_run(
    monkeypatch, tmp_path, module, resolver, dir_attr, setting
):
    monkeypatch.setattr(settings, setting, "")
    monkeypatch.setattr(module, dir_attr, tmp_path)  # no .venv inside
    assert resolver() == ["uv", "run", "--directory", str(tmp_path), "python"]


def test_b3run_puts_the_workspace_on_pythonpath(tmp_path, this_interpreter):
    """The whole point of the throwaway workspace: `python -m scripts.x` has to
    resolve to the copy in the workspace, not to anything installed."""
    (tmp_path / "probe.py").write_text(
        "import os, sys; print(os.environ['PYTHONPATH'])"
    )
    proc = b3run.run_module(tmp_path, ["probe"])
    assert proc.returncode == 0, proc.stderr
    assert str(tmp_path) in proc.stdout


def test_enginerun_pins_the_market_plane_dsn(monkeypatch, tmp_path, this_interpreter):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "probe.py").write_text("import os; print(os.environ['ENGINE_DATABASE_URL'])")
    monkeypatch.setattr(enginerun, "ENGINE", tmp_path)

    proc = enginerun.run_script("probe.py", [])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == settings.engine_database_url


def test_extra_env_is_merged_last(monkeypatch, tmp_path, this_interpreter):
    """ENGINE_USER_SPECS_DIR rides in this way; a caller must be able to add to
    the env without the runner's own keys clobbering it."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "probe.py").write_text("import os; print(os.environ.get('X_EXTRA', ''))")
    monkeypatch.setattr(enginerun, "ENGINE", tmp_path)

    proc = enginerun.run_script("probe.py", [], {"X_EXTRA": "set-by-caller"})
    assert proc.stdout.strip() == "set-by-caller"


def test_runners_do_not_raise_timeoutexpired(
    monkeypatch, tmp_path, instant_timeout, this_interpreter
):
    """Belt and braces: the exception type every caller is written not to catch."""
    _write_sleeper(tmp_path)
    monkeypatch.setattr(enginerun, "ENGINE", tmp_path)
    for call in (
        lambda: b3run.run_module(tmp_path, SLEEPER),
        lambda: enginerun.run_module(SLEEPER, timeout_s=1),
    ):
        try:
            call()
        except subprocess.TimeoutExpired:  # pragma: no cover - the bug we guard
            pytest.fail("runner leaked TimeoutExpired instead of returning 124")
