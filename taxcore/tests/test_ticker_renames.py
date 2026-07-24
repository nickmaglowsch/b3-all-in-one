"""Self-check for apply_ticker_renames: old->new mapping, *F suffix preserved."""
import pandas as pd

from scripts.load_b3 import apply_ticker_renames


def test_rename_preserves_fractional_suffix():
    df = pd.DataFrame({"ticker": ["TRPL4", "TRPL4F", "PETR4", "TRPL3"], "x": [1, 2, 3, 4]})
    out = apply_ticker_renames(df)
    assert list(out["ticker"]) == ["ISAE4", "ISAE4F", "PETR4", "ISAE3"]
    assert list(out["x"]) == [1, 2, 3, 4]  # other columns untouched


def test_empty_and_missing_column_are_noops():
    assert apply_ticker_renames(pd.DataFrame()).empty
    df = pd.DataFrame({"other": [1]})
    assert apply_ticker_renames(df).equals(df)


if __name__ == "__main__":
    test_rename_preserves_fractional_suffix()
    test_empty_and_missing_column_are_noops()
    print("ok")
