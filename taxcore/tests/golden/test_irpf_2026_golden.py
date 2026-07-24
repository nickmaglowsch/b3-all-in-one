"""Golden-file lock on scripts/irpf_2026.py.

Feeds the canonical trades/cash_events CSVs (+ the raw movimentacao extracts the
script reads directly for renda fixa) and asserts the generated IRPF markdown
matches the committed golden ``expected/irpf-2025.md``.

Both the golden and the extracts it derives from are synthetic — see
``make_fixtures.py`` and README.md.
"""
from __future__ import annotations

import pytest

from conftest import EXPECTED, assert_text_matches, build_workspace, run_script

pytestmark = pytest.mark.golden


def test_irpf_2025_matches_golden(tmp_path):
    ws = build_workspace(tmp_path, ["trades.csv", "cash_events.csv"])
    r = run_script(ws, ["scripts.irpf_2026", "--year", "2025"])
    assert r.returncode == 0, f"irpf_2026 failed:\n{r.stderr}"
    assert_text_matches(r.stdout, EXPECTED / "irpf-2025.md")
