"""Non-interactive / --json contract for scripts/tlh_plan.py.

Runs the script as a subprocess against the REAL vendored data (taxcore/data),
read-only: --json returns before the report-save block, so nothing is written.
Every run is offline-guarded (a sitecustomize on PYTHONPATH blocks any non-local
socket connect) to prove the planner stays offline on these fixtures.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

from scripts.irpf_2026 import build_timeline
from scripts.tlh_plan import ISENCAO_LIMIT, walk_history

# tests -> taxcore
TAXCORE = Path(__file__).resolve().parents[1]

# Same offline guard the golden suite injects: any connect() off-box fails loud.
_NETGUARD = '''\
import socket
try:
    import coverage
    coverage.process_startup()
except ImportError:
    pass
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


def _coverage_env() -> dict:
    """Measure the child too, but only when the parent is under coverage —
    otherwise a plain pytest run litters the repo with `.coverage.*` files."""
    try:
        import coverage
    except ImportError:
        return {}
    if coverage.Coverage.current() is None:
        return {}
    return {"COVERAGE_PROCESS_START": str(TAXCORE / "pyproject.toml"),
            "COVERAGE_FILE": str(TAXCORE / ".coverage")}


def run_tlh(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """`python -m scripts.tlh_plan [args]` in the real tree, offline, no stdin."""
    root = cwd or TAXCORE
    env = {**os.environ, "PYTHONPATH": f"{root}{os.pathsep}{_GUARD_DIR}",
           "PYTHONWARNINGS": "ignore", **_coverage_env()}
    return subprocess.run(
        [sys.executable, "-m", "scripts.tlh_plan", *args],
        cwd=root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
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


def test_json_reports_why_and_the_carry_left_over():
    """(b2) Every bucket carries its rationale and the prejuízo left after the
    plan. Without them a loss-harvesting sell renders as a bare "Nenhum TLH
    necessário" with carry_used 0 — the plan looks like it did nothing, when it
    just moved the whole loss into the carryforward."""
    r = run_tlh(["--json", "--sells", "PETR4:459"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)

    b = result["buckets"]["AÇÃO"]
    carry = result["situation"]["AÇÃO"]["carry"]
    assert b["rationale"].strip(), "the status alone does not say why"
    # Carry spent comes off; a final PnL below zero goes back on.
    assert b["carry_after"] == pytest.approx(
        carry - b["carry_used"] + max(0.0, -b["final_pnl"]))
    assert b["carry_after"] >= 0.0
    # This book harvests down to a taxable residual, so the carry is consumed.
    assert b["carry_after"] == pytest.approx(carry - b["carry_used"])


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


# --------------------------------------------------------------------------
# The markdown side. --json is what the platform calls; a human runs the CLI,
# and none of that path — the decision branches, the report, the file it
# writes — is reachable through the JSON contract above.
# --------------------------------------------------------------------------

def test_no_sells_prints_the_situation_and_stops():
    """stdin closed: the interactive prompt gets EOF on the first read, which has
    to end the session cleanly rather than loop or traceback."""
    r = run_tlh([])
    assert r.returncode == 0, r.stderr
    assert "Situação MTD" in r.stdout
    assert "Nenhuma venda informada" in r.stdout


def test_a_sale_under_the_exemption_reports_it_as_isento_and_plans_nothing():
    r = run_tlh(["--json", "--sells", "PETR4:100"])
    assert r.returncode == 0, r.stderr
    b = json.loads(r.stdout)["buckets"]["AÇÃO"]
    assert b["gross_after"] <= ISENCAO_LIMIT
    assert b["status"] == "Nenhum TLH necessário"
    assert b["ir"] == 0.0 and b["carry_used"] == 0.0 and b["harvest"] == []
    assert "isento" in b["rationale"]


def test_a_loss_making_sale_needs_no_harvest_and_grows_the_carry():
    """FIIs have no exemption, so a losing FII month lands straight on the
    'already negative' branch — and the loss has to show up as carry gained."""
    r = run_tlh(["--json", "--sells", "RECR11:150"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    b = result["buckets"]["FII"]
    carry_before = result["situation"]["FII"]["carry"]
    assert b["pnl_after"] < 0
    assert b["status"] == "Nenhum TLH necessário"
    assert b["ir"] == 0.0 and b["harvest"] == []
    assert b["carry_after"] == pytest.approx(carry_before - b["pnl_after"])


def test_a_gain_the_carry_already_covers_is_left_alone():
    """Harvesting here would sell a position to offset a gain that is already
    offset — the carry is spent instead, and no order is emitted.

    SBSP3:300, not :100, since the October IVVB11 gain started drawing on the
    same operações comuns carry (R$ 1.723 → R$ 1.008): the sale has to net less."""
    r = run_tlh(["--json", "--sells", "VALE3:250,SBSP3:300"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    b, carry = result["buckets"]["AÇÃO"], result["situation"]["AÇÃO"]["carry"]
    # The fixture book is what puts this case in reach; if it drifts, say so here
    # rather than in an unexplained status mismatch below.
    assert b["gross_after"] > ISENCAO_LIMIT and 0 < b["pnl_after"] <= carry, (
        "fixture book no longer produces a carry-covered taxable month")
    assert b["status"] == "Nenhum TLH necessário"
    assert b["carry_used"] == pytest.approx(b["pnl_after"])
    assert b["ir"] == 0.0 and b["harvest"] == []


def test_excluding_every_loser_reports_the_tax_instead_of_inventing_a_plan():
    losers = "BBAS3,EGIE3,KLBN11,SBSP3,CMIG4,ITUB4,ABEV3,B3SA3,GOAU4,ISAE4,WEGE3,TAEE11"
    r = run_tlh(["--json", "--exclude", losers, "--sells", "PETR4:700"])
    assert r.returncode == 0, r.stderr
    b = json.loads(r.stdout)["buckets"]["AÇÃO"]
    assert b["status"] == "TLH desejável mas sem candidatos"
    assert b["harvest"] == []
    # The whole residual is taxable at 15%, and the report has to say the number.
    assert b["ir"] == pytest.approx((b["pnl_after"] - b["carry_used"]) * 0.15)
    assert b["ir"] > 0


def test_no_save_prints_the_report_without_writing_one():
    before = {p.name for p in (TAXCORE / "reports").glob("*.md")}
    r = run_tlh(["--sells", "PETR4:700", "--no-save"])
    assert r.returncode == 0, r.stderr
    assert "# TLH plan" in r.stdout and "T+1" in r.stdout
    assert {p.name for p in (TAXCORE / "reports").glob("*.md")} == before


def test_a_second_report_on_the_same_day_does_not_overwrite_the_first(tmp_path):
    """The filename is the date, so without the suffix loop the morning's plan is
    silently replaced by the afternoon's."""
    ws = tmp_path / "ws"
    (ws / "reports").mkdir(parents=True)
    shutil.copytree(TAXCORE / "scripts", ws / "scripts")
    shutil.copytree(TAXCORE / "data", ws / "data")

    for _ in range(2):
        r = run_tlh(["--sells", "PETR4:700"], cwd=ws)
        assert r.returncode == 0, r.stderr
        assert "Relatório salvo" in r.stdout

    written = {p.name for p in (ws / "reports").glob("*.md")}
    stem = f"{date.today().isoformat()}-tlh-plan"
    assert written == {f"{stem}.md", f"{stem}-2.md"}
