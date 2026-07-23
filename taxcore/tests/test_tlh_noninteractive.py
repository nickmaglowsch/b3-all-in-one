"""Non-interactive / --json contract for scripts/tlh_plan.py.

Runs the script as a subprocess against the REAL vendored data (taxcore/data),
read-only: --json returns before the report-save block, so nothing is written.
Every run is offline-guarded (a sitecustomize on PYTHONPATH blocks any non-local
socket connect) to prove the planner stays offline on these fixtures.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scripts.irpf_2026 import build_timeline
from scripts.tlh_plan import ISENCAO_LIMIT, walk_history

# tests -> taxcore
TAXCORE = Path(__file__).resolve().parents[1]

# Same offline guard the golden suite injects: any connect() off-box fails loud.
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

_GUARD_DIR = Path(tempfile.mkdtemp())
(_GUARD_DIR / "sitecustomize.py").write_text(_NETGUARD)


def run_tlh(args: list[str]) -> subprocess.CompletedProcess:
    """`python -m scripts.tlh_plan [args]` in the real tree, offline, no stdin."""
    env = {**os.environ, "PYTHONPATH": str(_GUARD_DIR), "PYTHONWARNINGS": "ignore"}
    return subprocess.run(
        [sys.executable, "-m", "scripts.tlh_plan", *args],
        cwd=TAXCORE, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )


def test_json_no_sells_reports_situation():
    """(a) --json with no sells → valid JSON whose per-bucket situation
    (carry + MTD) matches a fresh walk_history()."""
    r = run_tlh(["--json"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)  # raises if not valid JSON

    carry, mtd = walk_history(build_timeline())
    for cls in ("AÇÃO", "FII"):
        sit = result["situation"][cls]
        assert sit["carry"] == pytest.approx(carry[cls])
        assert sit["gross_mtd"] == pytest.approx(mtd[cls]["gross"])
        assert sit["pnl_mtd"] == pytest.approx(mtd[cls]["pnl"])
    # No sells proposed → nothing to plan.
    assert result["sells"] == []
    assert result["buckets"] == {}


def test_json_gain_triggers_consistent_harvest():
    """(b) Selling all of a real gainer (PETR4) pushes AÇÃO gross past the
    R$20k isenção into a taxable residual → a harvest plan appears whose
    numbers are internally consistent."""
    r = run_tlh(["--json", "--sells", "PETR4:459"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)

    (sell,) = result["sells"]
    assert sell["ticker"] == "PETR4" and sell["cls"] == "AÇÃO"
    assert sell["pnl"] > 0  # it's a gainer

    b = result["buckets"]["AÇÃO"]
    carry = result["situation"]["AÇÃO"]["carry"]
    # Harvest only fires past isenção and only on a residual taxable gain.
    assert b["gross_after"] > ISENCAO_LIMIT
    residual = b["pnl_after"] - carry
    assert residual > 0
    assert b["status"] == "TLH recomendado"
    assert b["harvest"], "expected at least one harvest sell"

    # target_loss == -(residual); harvested loss moves pnl toward zero.
    assert b["target_loss"] == pytest.approx(-residual)
    harvested = sum(h["realized_loss"] for h in b["harvest"])
    assert harvested < 0  # losses are negative
    assert b["final_pnl"] == pytest.approx(b["pnl_after"] + harvested)
    # Rebuys mirror the harvest sells one-for-one (T+1, no wash-sale rule).
    assert [h["ticker"] for h in b["rebuys"]] == [h["ticker"] for h in b["harvest"]]
    assert [h["qty"] for h in b["rebuys"]] == [h["qty"] for h in b["harvest"]]


@pytest.mark.parametrize("spec,needle", [
    ("FAKE3:100", "positions.csv"),      # ticker not held
    ("PETR4:999999", "cotas"),            # qty > held
    ("PETR4-100", "formato"),             # bad token format
    ("PETR4:abc", "quantidade"),          # non-numeric qty
])
def test_invalid_sells_exit_nonzero(spec, needle):
    """(c) Any bad --sells token → nonzero exit + a clear stderr message."""
    r = run_tlh(["--json", "--sells", spec])
    assert r.returncode != 0
    assert needle in r.stderr
    assert r.stdout.strip() == ""  # no JSON emitted on error
