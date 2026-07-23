"""Registry hook for platform-materialized user specs (ENGINE_USER_SPECS_DIR).

Guards the two contracts the injection loop must hold: a user spec dropped in
the env-named dir is discovered, and a user spec whose name collides with a
built-in is skipped (built-ins win). Specs are written as JSON-into-.yaml — the
platform materializes them that way (JSON is valid YAML), so this proves the
loader accepts it.
"""
from __future__ import annotations

import json

from backtests.core.strategy_registry import StrategyRegistry
from backtests.core.config_strategy import RankAndHold, FixedWeight


def _write(dir_, fname, spec):
    # JSON dumped into a .yaml file — exactly how the platform materializes specs.
    (dir_ / fname).write_text(json.dumps(spec))


def test_user_specs_dir_registers_and_built_in_wins(tmp_path, monkeypatch):
    _write(tmp_path, "abc123.yaml", {
        "name": "MyUserMomentum", "kind": "rank_and_hold", "rebalance": "ME",
        "universe": {"min_adtv": 1000000, "min_price": 1.0},
        "signal": {"factor": "momentum", "lookback": 6},
        "selection": {"top_pct": 0.10, "min_names": 5},
    })
    # Collides with the built-in low_vol.yaml (name: LowVol); made a different
    # kind so we can tell which instance survived.
    _write(tmp_path, "def456.yaml", {
        "name": "LowVol", "kind": "fixed_weight", "rebalance": "QE",
        "sleeves": [{"asset": "CDI", "weight": 1.0}],
    })

    monkeypatch.setenv("ENGINE_USER_SPECS_DIR", str(tmp_path))
    reg = StrategyRegistry()
    reg.discover()

    # user spec discovered
    assert "MyUserMomentum" in reg.names()
    assert isinstance(reg.get("MyUserMomentum"), RankAndHold)
    # built-in wins the name collision (still the RankAndHold from specs/, not
    # the user's FixedWeight)
    assert isinstance(reg.get("LowVol"), RankAndHold)


def test_no_env_var_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGINE_USER_SPECS_DIR", raising=False)
    reg = StrategyRegistry()
    reg.discover()
    assert "MyUserMomentum" not in reg.names()
    assert "LowVol" in reg.names()   # built-ins still there
