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


# ── A named signal can read fundamentals too ──────────────────────────────────
# signals.yaml gained book_to_price_pit / net_margin_pit / ebitda_margin_pit, so
# "resolves to a name in signals.yaml" stopped implying "reads no f_* frame".
# Before this, a `factor:` reference to one of them reported
# needs_fundamentals: False and sailed past the no-fundamentals market guard.

def test_a_named_signal_that_reads_fundamentals_is_flagged():
    r = validate(_rank({"factor": "book_to_price_pit"}))
    assert r["ok"] and r["needs_fundamentals"]


def test_a_named_signal_that_reads_no_fundamentals_is_not_flagged():
    r = validate(_rank({"factor": "low_vol", "lookback": 12}))
    assert r["ok"] and not r["needs_fundamentals"]


def test_a_fundamentals_named_signal_is_rejected_on_a_market_without_them():
    r = validate(_rank({"factor": "net_margin_pit"}, market="CRYPTO"))
    assert not r["ok"]
    assert any("no fundamentals" in e for e in r["errors"])


def test_the_same_signal_is_accepted_on_a_market_that_has_them():
    for market in ("B3", "US"):
        r = validate(_rank({"factor": "net_margin_pit"}, market=market))
        assert r["ok"], (market, r["errors"])
        assert r["needs_fundamentals"]


def test_an_unknown_market_is_rejected_rather_than_defaulting_to_b3():
    r = validate(_rank({"factor": "momentum", "lookback": 6}, market="USA"))
    assert not r["ok"]
    assert any("Unknown market" in e for e in r["errors"])


def test_an_unknown_function_is_rejected_at_save_not_at_run():
    r = validate(_rank({"expr": "roll_summ(ret, lookback)", "lookback": 6}))
    assert not r["ok"]
    assert any("unknown function(s) ['roll_summ']" in e for e in r["errors"])


def test_every_documented_function_passes():
    expr = ("nan_add(rank(zscore(roll_sum(shift(log_ret, 1), lookback))), "
            "demean(-at_rebalance(ewm_std(pct_change(adj_close), 21))))")
    assert validate(_rank({"expr": expr, "lookback": 6}))["ok"]


_GATE = {"inputs": {"trend": {"flag": "bench_above", "offset": 1}},
         "rules": [{"trend": True}, {"min_true": 1, "top_pct": 0.05},
                   {"default": True, "park": True}]}


def test_a_well_formed_regime_is_ok():
    assert validate(_rank({"factor": "momentum"}, regime=_GATE))["ok"]


def test_a_rule_on_an_undeclared_input_is_rejected():
    regime = {**_GATE, "rules": [{"trnd": True}, {"default": True, "park": True}]}
    r = validate(_rank({"factor": "momentum"}, regime=regime))
    assert not r["ok"] and any("['trnd'] is not a declared input" in e for e in r["errors"])


def test_a_regime_input_without_a_flag_is_rejected():
    regime = {**_GATE, "inputs": {"trend": {"offset": 1}}}
    r = validate(_rank({"factor": "momentum"}, regime=regime))
    assert not r["ok"] and any("needs a `flag`" in e for e in r["errors"])


def test_a_regime_without_rules_is_rejected():
    for regime in ({"inputs": {}}, ["not", "a", "mapping"]):
        r = validate(_rank({"factor": "momentum"}, regime=regime))
        assert not r["ok"] and any("`rules` list" in e for e in r["errors"])
