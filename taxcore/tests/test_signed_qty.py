"""signed_qty and the option-premium sign, computed column-wise.

Four apply(axis=1) calls built these one row at a time. The golden tests already
pin the values byte-for-byte on real fixtures; what they cannot reach is the
empty-input case, where DataFrame.apply(axis=1) returns an empty *DataFrame*
rather than a Series and np.where returns an empty array. A B3 extract with no
trades at all (a movimentação-only export) hits exactly that.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.load_b3 import normalize  # noqa: E402


def _raw(rows):
    return pd.DataFrame(rows, columns=[
        "date", "side", "market", "maturity", "broker", "ticker",
        "qty", "price", "amount", "__source_file"])


def test_buys_are_positive_and_sells_negative():
    df = normalize(_raw([
        ["2024-01-02", "Compra", "Mercado à Vista", "-", "XP", "PETR4",
         100.0, 30.0, 3000.0, "f.xlsx"],
        ["2024-01-03", "Venda", "Mercado à Vista", "-", "XP", "PETR4",
         40.0, 31.0, 1240.0, "f.xlsx"],
    ]))
    assert list(df["signed_qty"]) == [100.0, -40.0]


def test_side_matching_is_case_and_whitespace_insensitive():
    # normalize() strips and lowercases before testing the prefix; a "COMPRA" or
    # " Compra " from a differently-formatted export must still read as a buy.
    df = normalize(_raw([
        ["2024-01-02", " COMPRA ", "Mercado à Vista", "-", "XP", "PETR4",
         10.0, 30.0, 300.0, "f.xlsx"],
        ["2024-01-02", "venda", "Mercado à Vista", "-", "XP", "PETR4",
         5.0, 30.0, 150.0, "f.xlsx"],
    ]))
    assert list(df["signed_qty"]) == [10.0, -5.0]


def test_empty_input_still_yields_a_signed_qty_column():
    df = normalize(_raw([]))
    assert "signed_qty" in df.columns
    assert len(df) == 0


def test_signed_qty_is_numeric_not_object():
    """apply(axis=1) could hand back an object column; downstream arithmetic
    (positions, the platform's index rebuild) assumes a float."""
    df = normalize(_raw([
        ["2024-01-02", "Compra", "Mercado à Vista", "-", "XP", "PETR4",
         100.0, 30.0, 3000.0, "f.xlsx"],
    ]))
    assert np.issubdtype(df["signed_qty"].dtype, np.floating)
