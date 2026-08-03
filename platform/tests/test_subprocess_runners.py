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

import json
import subprocess
import sys
from pathlib import Path

import pytest

from platform_app import b3run, enginerun, subprocs
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


def test_ingestion_gets_the_long_timeout_not_the_request_one(
    monkeypatch, tmp_path, this_interpreter
):
    """run_module defaults to ingest_timeout_s (6h), not subprocess_timeout_s (600s).

    The difference is the whole reason run_module exists separately: b3_pipeline.main
    and cvm_main download years of history and legitimately run for hours, while a
    request-path subprocess must not hold a worker slot for more than minutes. Wiring
    run_module to the request timeout would kill every ingestion at 10 minutes — with
    a *successful-looking* exit 124 that the caller reports as an engine failure.

    Only ingest_timeout_s is squeezed here; subprocess_timeout_s stays long, so this
    fails if the default is ever sourced from the wrong setting.
    """
    _write_sleeper(tmp_path)
    monkeypatch.setattr(enginerun, "ENGINE", tmp_path)
    monkeypatch.setattr(settings, "ingest_timeout_s", 1)
    monkeypatch.setattr(settings, "subprocess_timeout_s", 600)

    proc = enginerun.run_module(SLEEPER)  # no timeout_s — exercise the default

    assert proc.returncode == 124, (
        "run_module did not use ingest_timeout_s for its default timeout"
    )
    assert "after 1s" in proc.stderr, (
        f"timed out against the wrong setting: {proc.stderr!r}"
    )


def test_request_path_runners_get_the_short_timeout(
    monkeypatch, tmp_path, this_interpreter
):
    """The mirror of the above: run_module's siblings must NOT pick up the 6h value."""
    _write_sleeper(tmp_path)
    monkeypatch.setattr(settings, "subprocess_timeout_s", 1)
    monkeypatch.setattr(settings, "ingest_timeout_s", 6 * 60 * 60)

    proc = b3run.run_module(tmp_path, SLEEPER)

    assert proc.returncode == 124
    assert "after 1s" in proc.stderr, (
        f"b3run used the ingestion timeout instead of the request one: {proc.stderr!r}"
    )


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
    "resolver, setting",
    [
        (b3run._taxcore_python, "taxcore_python"),
        (enginerun._engine_python, "engine_python"),
    ],
)
def test_explicit_interpreter_setting_wins(monkeypatch, resolver, setting):
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


# ── What a tenant workspace is allowed to contain ─────────────────────────────
# Every import, IRPF report and TLH plan builds a fresh workspace and copies
# taxcore into it, so whatever taxcore ships is copied per run, per tenant. The
# platform shells exactly three modules; anything else in taxcore/scripts is
# carried by every one of those copies to be imported by nobody.
#
# This is the invariant the vendored tree is trimmed against, so it is pinned
# here rather than left to a reviewer's memory: adding a script to taxcore is
# fine, adding one the platform does not shell should be a decision, not a
# silent per-run copy.

SHELLED_MODULES = {"load_b3", "irpf_2026", "tlh_plan"}


def test_seed_workspace_copies_only_what_the_shelled_modules_import(tmp_path):
    b3run.seed_workspace(tmp_path)
    modules = {p.stem for p in (tmp_path / "scripts").glob("*.py")} - {"__init__"}
    # _common and apuracao are imported by the three above; nothing else may ride along.
    assert modules == SHELLED_MODULES | {"_common", "apuracao"}, (
        f"unshelled taxcore scripts copied into every tenant workspace: "
        f"{sorted(modules - SHELLED_MODULES - {'_common', 'apuracao'})}"
    )


def test_seed_workspace_does_not_copy_the_methodologies_package(tmp_path):
    """No shelled module imports `methodologies` — only the ranking scripts did."""
    b3run.seed_workspace(tmp_path)
    assert not (tmp_path / "methodologies").exists()


# ── One JSON reader for both stdout conventions ───────────────────────────────
# The engine scripts print progress lines and then one line of JSON; taxcore's
# load_b3 pretty-prints its summary across many lines. Two readers used to exist
# because each only handled its own shape. These pin that one reader handles
# both — and that the two error contracts stayed distinct: a caller that writes
# an Import row from the payload wants {}, a caller answering an HTTP request
# wants the raise.

def test_last_json_reads_the_engines_one_line_payload_after_progress_lines():
    text = 'loading prices...\ndone\n{"nav": 1000, "ok": true}\n'
    assert subprocs.last_json(text) == {"nav": 1000, "ok": True}


def test_last_json_reads_load_b3s_pretty_printed_payload():
    text = 'parsing\n' + json.dumps({"files_loaded": ["a.xlsx"], "kind": "neg"}, indent=2)
    assert subprocs.last_json(text) == {"files_loaded": ["a.xlsx"], "kind": "neg"}


def test_last_json_ignores_a_log_line_printed_after_the_payload():
    text = '{"nav": 1}\nWARNING: something\n'
    assert subprocs.last_json(text) == {"nav": 1}


def test_last_json_returns_empty_by_default_when_there_is_no_payload():
    assert subprocs.last_json("no json here\n") == {}


def test_last_json_strict_raises_when_there_is_no_payload():
    with pytest.raises(ValueError, match="no JSON object"):
        subprocs.last_json("no json here\n", strict=True)
