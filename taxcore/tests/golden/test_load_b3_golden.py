"""Golden-file lock on scripts/load_b3.py.

Runs the parser over the real B3 extracts in ``b3_imports/`` and asserts the
produced trades/cash_events/positions CSVs match the canonical vendored files
in ``taxcore/data/``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from conftest import DATA, assert_csv_matches, build_workspace, run_script

pytestmark = pytest.mark.golden

OUTPUTS = ("trades.csv", "cash_events.csv", "positions.csv")


def test_default_invocation_reproduces_canonical(tmp_path):
    """`python -m scripts.load_b3` (default flags) reproduces all three canonical
    CSVs exactly. This is the invocation the vendored data/ was generated with."""
    ws = build_workspace(tmp_path, ["cost_basis_overrides.csv"])
    r = run_script(ws, ["scripts.load_b3"])
    assert r.returncode == 0, f"load_b3 failed:\n{r.stderr}"
    for name in OUTPUTS:
        assert_csv_matches(ws / "data" / name, DATA / name)


def test_all_markets_trades_and_events_are_flag_invariant(tmp_path):
    """`--all-markets` only widens positions.csv (adds renda-fixa / options /
    futures). trades.csv and cash_events.csv are identical to the default run,
    and positions is a strict superset of the canonical set."""
    ws = build_workspace(tmp_path, ["cost_basis_overrides.csv"])
    r = run_script(ws, ["scripts.load_b3", "--all-markets"])
    assert r.returncode == 0, f"load_b3 --all-markets failed:\n{r.stderr}"

    assert_csv_matches(ws / "data" / "trades.csv", DATA / "trades.csv")
    assert_csv_matches(ws / "data" / "cash_events.csv", DATA / "cash_events.csv")

    got = set(pd.read_csv(ws / "data" / "positions.csv")["ticker"])
    canonical = set(pd.read_csv(DATA / "positions.csv")["ticker"])
    assert canonical <= got, f"--all-markets dropped canonical positions: {canonical - got}"
