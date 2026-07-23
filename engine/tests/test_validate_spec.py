"""Contract for scripts/validate_spec.py — the on-save validator the platform
shells. Proves the structured report shape and each rejection path, with no
market-data load.
"""
from __future__ import annotations

from scripts.validate_spec import validate


def _rank(signal: dict, **extra) -> dict:
    spec = {
        "name": "T", "kind": "rank_and_hold", "rebalance": "ME",
        "universe": {"min_adtv": 1000000, "min_price": 1.0},
        "signal": signal,
        "selection": {"top_pct": 0.10, "min_names": 5},
    }
    spec.update(extra)
    return spec


def test_valid_named_factor_is_ok():
    r = validate(_rank({"factor": "momentum", "lookback": 6}))
    assert r == {"ok": True, "errors": [], "needs_fundamentals": False}


def test_valid_inline_expr_is_ok():
    r = validate(_rank({"expr": "roll_sum(shift(log_ret, 1), lookback)", "lookback": 6}))
    assert r["ok"] and not r["needs_fundamentals"]


def test_valid_shared_data_key_factor_is_ok():
    r = validate(_rank({"factor": "mf_composite"}))
    assert r["ok"] and not r["needs_fundamentals"]


def test_bad_expr_syntax_is_rejected():
    r = validate(_rank({"expr": "ret.values"}))     # attribute access — off the whitelist
    assert not r["ok"]
    assert any("disallowed syntax" in e for e in r["errors"])


def test_unknown_factor_is_rejected():
    r = validate(_rank({"factor": "does_not_exist"}))
    assert not r["ok"]
    assert any("unknown factor" in e for e in r["errors"])


def test_store_reference_is_rejected():
    r = validate(_rank({"factor": "store:some__feature__id"}))
    assert not r["ok"]
    assert any("store:" in e for e in r["errors"])


def test_fundamentals_factor_sets_needs_fundamentals():
    r = validate(_rank({"factor": "f_pe_ratio_dyn"}))
    assert r["ok"] and r["needs_fundamentals"]


def test_fundamentals_in_inline_expr_is_detected():
    r = validate(_rank({"expr": "roll_mean(f_roe_dyn, lookback)", "lookback": 3}))
    assert r["ok"] and r["needs_fundamentals"]


def test_fixed_weight_spec_is_ok():
    r = validate({
        "name": "P", "kind": "fixed_weight", "rebalance": "QE",
        "sleeves": [{"asset": "CDI", "weight": 1.0}],
    })
    assert r == {"ok": True, "errors": [], "needs_fundamentals": False}


def test_unknown_kind_is_rejected():
    r = validate({"name": "X", "kind": "bogus"})
    assert not r["ok"]
    assert any("unknown kind" in e for e in r["errors"])
