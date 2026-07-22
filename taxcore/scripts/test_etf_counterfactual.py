"""Self-check for mix_unit_price quarterly rebalancing."""
import pandas as pd

from scripts.etf_counterfactual import mix_unit_price


def test_periodic_rebalance():
    grid = pd.date_range("2020-01-01", "2020-12-31", freq="D")
    up = pd.Series([1.001 ** n for n in range(len(grid))], index=grid)  # +gentle
    flat = pd.Series(1.0, index=grid)
    legs = {"up": up, "flat": flat}
    w = {"up": 0.5, "flat": 0.5}

    q = mix_unit_price(w, legs, grid, "Q")
    assert q.iloc[0] == 1.0
    assert q.notna().all()
    # Continuity: no jump across quarter boundaries (only smooth drift).
    assert q.diff().abs().max() < 0.05
    # A rebalanced mix of an always-up leg must end above 1.
    assert q.iloc[-1] > 1.0
    # Quarterly rebalance trims the winner vs never selling → below daily? no;
    # just assert it differs from daily to prove the branch is exercised.
    assert abs(q.iloc[-1] - mix_unit_price(w, legs, grid, "D").iloc[-1]) > 1e-6


if __name__ == "__main__":
    test_periodic_rebalance()
    print("ok")
