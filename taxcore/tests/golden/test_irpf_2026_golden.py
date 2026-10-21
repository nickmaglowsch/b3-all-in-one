"""Golden-file lock on scripts/irpf_2026.py.

Feeds the canonical trades/cash_events CSVs (+ the raw movimentacao extracts the
script reads directly for renda fixa) and asserts the generated IRPF markdown
matches the committed golden ``expected/irpf-2025.md``.

Both the golden and the extracts it derives from are synthetic — see
``make_fixtures.py`` and README.md.
"""
from __future__ import annotations

import json
import re

import pytest

from conftest import EXPECTED, assert_text_matches, build_workspace, run_script

pytestmark = pytest.mark.golden


def test_irpf_2025_matches_golden(tmp_path):
    ws = build_workspace(tmp_path, ["trades.csv", "cash_events.csv"])
    r = run_script(ws, ["scripts.irpf_2026", "--year", "2025"])
    assert r.returncode == 0, f"irpf_2026 failed:\n{r.stderr}"
    assert_text_matches(r.stdout, EXPECTED / "irpf-2025.md")


def test_json_apuracao_agrees_with_the_markdown_it_renders(tmp_path):
    """`--json` is what the platform's DARF ledger reads, and the golden above
    only locks the markdown. Both come from ``monthly_apuracao``; this asserts
    they still say the same thing, so the ledger can never bill a number the
    report does not show."""
    ws = build_workspace(tmp_path, ["trades.csv", "cash_events.csv"])
    md = run_script(ws, ["scripts.irpf_2026", "--year", "2025"])
    js = run_script(ws, ["scripts.irpf_2026", "--year", "2025", "--json"])
    assert js.returncode == 0, f"irpf_2026 --json failed:\n{js.stderr}"

    payload = json.loads(js.stdout)
    assert payload["year"] == 2025
    assert len(payload["apuracao"]) == 48  # 4 lines (AÇÃO, FII, BDR_ETF, DAYTRADE) × 12
    assert {r["year"] for r in payload["apuracao"]} == {2025}
    # One DARF 6015 a month, summing every line: what the ledger bills.
    assert [d["month"] for d in payload["darf"]] == list(range(1, 13))
    assert sum(d["imposto"] for d in payload["darf"]) == pytest.approx(
        sum(r["ir"] for r in payload["apuracao"]))
    assert set(payload["avisos"]) == {"descoberto", "fii_presumido", "carry_inicial"}

    def total(classe):
        return sum(r["ir"] for r in payload["apuracao"] if r["classe"] == classe)

    def reported(label):
        m = re.search(rf"\*\*Total IR sobre {label} em 2025\*\*: R\$ ([\d.,]+)", md.stdout)
        assert m, f"markdown has no '{label}' total"
        # `:,.2f` — en-US grouping, so the comma is the thousands separator.
        return float(m.group(1).replace(",", ""))

    assert total("AÇÃO") == pytest.approx(reported("AÇÕES"), abs=0.005)
    assert total("FII") == pytest.approx(reported("FIIs"), abs=0.005)
