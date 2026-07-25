"""Run taxcore's stdin/CSV scripts as subprocesses over per-tenant data.

The scripts (``scripts/load_b3.py``, ``scripts/irpf_2026.py``) resolve every path
from ``ROOT = parent of scripts/`` and read/write ``ROOT/data`` + ``ROOT/b3_imports``.
We don't touch taxcore: each run builds a throwaway workspace (copy of scripts +
methodologies + the tenant's inputs), runs the module there, and reads its outputs.
This is the same technique as ``taxcore/tests/golden/conftest.py``.
"""
from __future__ import annotations

import csv
import datetime
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

from platform_app.config import settings

# Host default: platform/platform_app/b3run.py -> b3-all-in-one/taxcore.
# Container/override: settings.taxcore_dir (TAXCORE_DIR env) -> /opt/taxcore.
_DEFAULT_TAXCORE = Path(__file__).resolve().parents[2] / "taxcore"
TAXCORE = Path(settings.taxcore_dir) if settings.taxcore_dir else _DEFAULT_TAXCORE
PARSER_VERSION = "taxcore.load_b3"
# Synthetic exit code for a subprocess we killed on the timeout (shell convention).
TIMEOUT_EXIT = 124

# Blocks any non-local network connect inside the subprocess (proves the scripts stay
# offline with these inputs). Auto-imported because the workspace lands on PYTHONPATH.
# Lifted from taxcore/tests/golden/conftest.py.
_NETGUARD = '''\
import socket
_orig = socket.socket.connect
def _blocked(self, address):
    host = address[0] if isinstance(address, (tuple, list)) else address
    if host not in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
        raise RuntimeError(f"OFFLINE GUARD blocked network connect to {address!r}")
    return _orig(self, address)
socket.socket.connect = _blocked
'''


def _taxcore_python() -> list[str]:
    """The interpreter that has taxcore's deps (pandas, openpyxl)."""
    if settings.taxcore_python:
        return [settings.taxcore_python]
    venv = TAXCORE / ".venv" / "bin" / "python"
    if venv.exists():
        return [str(venv)]
    # ponytail: fallback for CI/fresh checkouts without a prebuilt taxcore venv.
    return ["uv", "run", "--directory", str(TAXCORE), "python"]


def workspace_root(tenant_id: object) -> Path:
    """Parent dir for a tenant's throwaway workspaces.

    Under UPLOADS_DIR/{tenant_id}, not /tmp, so the residue is reachable by the
    erasure path. Every workspace holds the tenant's full trades.csv,
    cash_events.csv and a copy of their B3 extracts — a named person's complete
    trade history — and is cleaned in a `finally` that a SIGKILL (OOM, container
    restart) skips. purge_tenant_files already rmtree's UPLOADS_DIR/{tenant_id},
    so putting them here means "excluir minha conta" reaches them too instead of
    returning "eliminado" over a /tmp dir that still has the data.
    """
    root = Path(settings.uploads_dir) / str(tenant_id) / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def seed_workspace(ws: Path) -> Path:
    """Copy scripts + methodologies + the offline guard; make data/ and b3_imports/."""
    shutil.copytree(TAXCORE / "scripts", ws / "scripts")
    shutil.copytree(TAXCORE / "methodologies", ws / "methodologies")
    (ws / "data").mkdir(parents=True, exist_ok=True)
    (ws / "b3_imports").mkdir(parents=True, exist_ok=True)
    (ws / "sitecustomize.py").write_text(_NETGUARD)
    return ws


def run_module(ws: Path, module_args: list[str]) -> subprocess.CompletedProcess:
    """``python -m <module> [args...]`` inside ``ws`` with taxcore's interpreter.

    Timeout → exit code 124, same contract as enginerun.run_script: callers
    already branch on a non-zero return code, and an import that wedges must not
    hold a worker slot (there are two) or a request thread indefinitely.
    """
    env = {**os.environ, "PYTHONPATH": str(ws), "PYTHONWARNINGS": "ignore"}
    argv = [*_taxcore_python(), "-m", *module_args]
    try:
        return subprocess.run(
            argv, cwd=ws, env=env, capture_output=True, text=True,
            timeout=settings.subprocess_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, TIMEOUT_EXIT, stdout="",
            stderr=f"{module_args[0]} timed out after {settings.subprocess_timeout_s}s",
        )


# --- CSV <-> DB value helpers. repr() on floats is byte-identical to how the
# canonical CSVs were written (verified against taxcore/data/*.csv). ---

def to_float(s: str | None) -> float | None:
    return None if s in (None, "") else float(s)


def to_date(s: str | None) -> datetime.date | None:
    return None if s in (None, "") else datetime.date.fromisoformat(s)


def to_bool(s: str | None) -> bool | None:
    return None if s in (None, "") else (s == "True")


def _fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, datetime.date):
        return v.isoformat()
    return str(v)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_fmt(c) for c in r])
    path.write_text(buf.getvalue())
