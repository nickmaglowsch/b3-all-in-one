"""Shared plumbing for the golden-file regression suite.

The scripts under test (`scripts/load_b3.py`, `scripts/irpf_2026.py`) resolve every
path from ``ROOT = parent of scripts/`` and read/write ``ROOT/data`` + ``ROOT/b3_imports``.
Rather than refactor them, each test builds a throwaway copy of the tree in a
``tmp_path`` workspace, runs the script there as a subprocess, and compares its
outputs against the canonical vendored files in ``taxcore/data`` /
``taxcore/reports``.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# tests/golden -> tests -> taxcore
TAXCORE = Path(__file__).resolve().parents[2]
DATA = TAXCORE / "data"
REPORTS = TAXCORE / "reports"
EXPECTED = Path(__file__).resolve().parent / "expected"

# Auto-imported by every subprocess (it lands on PYTHONPATH). Proves the scripts
# stay offline with these fixtures AND enforces it: any non-local connect fails loud.
# ponytail: connect() hook, not a full sandbox — enough since the scripts do pure file IO.
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


def build_workspace(dst: Path, data_files: list[str]) -> Path:
    """Copy the minimal tree the scripts need into ``dst`` and return it.

    Always copies ``scripts/`` (the package the subprocess imports) and
    ``b3_imports/`` (the raw B3 extracts). ``data_files`` names the non-generated
    inputs to seed under ``data/`` (e.g. ``cost_basis_overrides.csv`` for load_b3,
    or the canonical ``trades.csv``/``cash_events.csv`` for irpf).
    """
    (dst / "data").mkdir(parents=True, exist_ok=True)
    shutil.copytree(TAXCORE / "scripts", dst / "scripts")
    shutil.copytree(TAXCORE / "b3_imports", dst / "b3_imports")
    for name in data_files:
        shutil.copy(DATA / name, dst / "data" / name)
    (dst / "sitecustomize.py").write_text(_NETGUARD)
    return dst


def run_script(workspace: Path, module_args: list[str]) -> subprocess.CompletedProcess:
    """Run ``python -m <module> [args...]`` inside ``workspace`` (offline-guarded)."""
    env = {**os.environ, "PYTHONPATH": str(workspace), "PYTHONWARNINGS": "ignore"}
    return subprocess.run(
        [sys.executable, "-m", *module_args],
        cwd=workspace, env=env, capture_output=True, text=True,
    )


def assert_csv_matches(produced: Path, expected: Path, atol: float = 1e-6) -> None:
    """pandas comparison: same columns/rows, order-independent, float-tolerant.

    Rows are stably sorted by every column so a reordering is not a failure;
    numeric cells compare with ``atol`` (NaN==NaN), everything else exactly.
    Raises with a readable per-cell diff on mismatch.
    """
    got = pd.read_csv(produced)
    exp = pd.read_csv(expected)
    assert list(got.columns) == list(exp.columns), (
        f"{produced.name}: column mismatch\n produced={list(got.columns)}\n expected={list(exp.columns)}"
    )
    assert len(got) == len(exp), (
        f"{produced.name}: row count produced={len(got)} expected={len(exp)}"
    )
    keys = list(exp.columns)
    got = got.sort_values(keys, kind="stable", na_position="last").reset_index(drop=True)
    exp = exp.sort_values(keys, kind="stable", na_position="last").reset_index(drop=True)

    diffs: list[str] = []
    for col in keys:
        g, e = got[col], exp[col]
        if pd.api.types.is_numeric_dtype(e) and pd.api.types.is_numeric_dtype(g):
            mism = ~np.isclose(g.to_numpy(float), e.to_numpy(float), atol=atol, rtol=0, equal_nan=True)
        else:
            mism = (g.astype("string").fillna("<NA>") != e.astype("string").fillna("<NA>")).to_numpy()
        for i in np.nonzero(mism)[0]:
            diffs.append(f"  row {i} · {col!r}: produced={got.at[i, col]!r} expected={exp.at[i, col]!r}")

    assert not diffs, (
        f"{produced.name}: {len(diffs)} cell diff(s) vs {expected}:\n" + "\n".join(diffs[:40])
    )


def normalize_irpf(text: str) -> str:
    """Neutralize the one volatile line (``**Date generated**: <today>``)."""
    return re.sub(r"(?m)^\*\*Date generated\*\*:.*$", "**Date generated**: <DATE>", text)


def assert_text_matches(produced: str, golden: Path) -> None:
    got = normalize_irpf(produced)
    exp = normalize_irpf(golden.read_text())
    if got != exp:
        diff = "\n".join(difflib.unified_diff(
            exp.splitlines(), got.splitlines(),
            fromfile=str(golden), tofile="produced", lineterm=""))
        pytest.fail(f"irpf output diverged from golden:\n{diff}")
