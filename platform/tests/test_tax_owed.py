"""`_owed_today` — IR actually owed on the month as it stands.

Pure math, no DB, no fixtures: the function takes a plain tlh_plan payload.

This exists because the obvious field is the wrong one. `buckets[cls].ir` means
three different things depending on which branch of tlh_plan produced it, and in
the "TLH recomendado" branch it is the IR *after executing a harvest the user has
not executed* — R$ 0,00, very often, precisely when the real DARF is largest.
Reading that as "devido" prints zero to someone who owes money. These tests pin
the derivation to `situation`, which is plain month-to-date fact.

    PLATFORM_TEST_DB=platform_test_owed uv run pytest tests/test_tax_owed.py
"""
from __future__ import annotations

import pytest

from platform_app.routes import _owed_today

LIMIT = 20_000.0
RATES = {"AÇÃO": 0.15, "FII": 0.20}


def payload(cls: str, *, gross: float, pnl: float, carry: float = 0.0) -> dict:
    return {
        "isencao_limit": LIMIT,
        "rates": RATES,
        "situation": {cls: {"gross_mtd": gross, "pnl_mtd": pnl, "carry": carry}},
    }


def test_acoes_at_exactly_the_limit_are_still_isentas():
    """The engine's rule is `gross <= ISENCAO_LIMIT`, inclusive. Exactly R$20.000
    of gross sales is tax-free — the boundary the UI got wrong with `>=`."""
    out = _owed_today(payload("AÇÃO", gross=20_000.0, pnl=5_000.0))["AÇÃO"]
    assert out["isento"] is True
    assert out["ir"] == 0.0


def test_one_centavo_over_the_limit_is_taxable():
    out = _owed_today(payload("AÇÃO", gross=20_000.01, pnl=5_000.0))["AÇÃO"]
    assert out["isento"] is False
    assert out["ir"] == pytest.approx(750.0)  # 5.000 × 15%


def test_the_harvest_branch_scenario_reports_real_tax():
    """The exact case where `buckets.ir` would have said R$ 0,00: gross over the
    limit, a large gain, and a carry that only partly covers it. tlh_plan would
    plan R$25k of harvest and report ir=0 post-harvest; nothing has been sold, so
    R$25.000 × 15% is owed today."""
    out = _owed_today(payload("AÇÃO", gross=50_000.0, pnl=30_000.0, carry=5_000.0))["AÇÃO"]
    assert out["carry_used"] == 5_000.0
    assert out["taxable"] == pytest.approx(25_000.0)
    assert out["ir"] == pytest.approx(3_750.0)


def test_carry_larger_than_the_gain_zeroes_the_tax_without_going_negative():
    out = _owed_today(payload("AÇÃO", gross=50_000.0, pnl=1_000.0, carry=9_999.0))["AÇÃO"]
    assert out["carry_used"] == 1_000.0  # only what the gain can absorb
    assert out["ir"] == 0.0


def test_a_losing_month_owes_nothing_and_consumes_no_carry():
    out = _owed_today(payload("AÇÃO", gross=50_000.0, pnl=-4_000.0, carry=1_000.0))["AÇÃO"]
    assert out["carry_used"] == 0.0
    assert out["ir"] == 0.0


def test_fiis_have_no_isencao_at_any_volume():
    """A FII sale is taxable from the first real, however small — the asymmetry
    the two-bucket layout exists to teach."""
    out = _owed_today(payload("FII", gross=100.0, pnl=1_000.0))["FII"]
    assert out["isento"] is False
    assert out["ir"] == pytest.approx(200.0)  # 20%


def test_missing_rate_or_empty_situation_does_not_invent_a_number():
    assert _owed_today({"situation": {}}) == {}
    out = _owed_today({"isencao_limit": LIMIT, "rates": {},
                       "situation": {"FII": {"gross_mtd": 1.0, "pnl_mtd": 500.0,
                                             "carry": 0.0}}})["FII"]
    assert out["ir"] == 0.0
