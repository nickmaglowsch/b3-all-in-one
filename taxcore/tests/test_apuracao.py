"""`monthly_apuracao` — the per-month DARF apuração, as data instead of markdown.

The rules used to live inline in the three render loops of `irpf_2026.main()`,
which meant the only way to read a month's IR was to parse "R$ 3.750,00" back out
of a markdown table. The platform's DARF ledger needs the numbers, so the loops
now consume this function and render what it returns.

These tests pin the rules the render loops were carrying — especially the
best-of-both-worlds isenção, whose asymmetry (isento *gains* skip the carry,
isento *losses* still feed it) is the thing a rewrite would silently flatten.

    uv run pytest tests/test_apuracao.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.irpf_2026 import CLASSES_APURACAO, monthly_apuracao  # noqa: E402

CLASSES = ("AÇÃO", "FII", "BDR", "ETF_RV", "ETF_RF")


def grid(**by_month) -> pd.DataFrame:
    """monthly_df as main() builds it: 12 rows, `{cls}_gross`/`{cls}_pnl` per class.

    Call as grid(**{"3": {"AÇÃO": (25_000, 5_000)}}) — month number as the kwarg,
    (gross, pnl) per class. Every unnamed month/class is a flat zero.
    """
    rows = []
    for m in range(1, 13):
        row = {"month": m}
        spec = by_month.get(str(m), {})
        for cls in CLASSES:
            gross, pnl = spec.get(cls, (0.0, 0.0))
            row[f"{cls}_gross"] = float(gross)
            row[f"{cls}_pnl"] = float(pnl)
        rows.append(row)
    return pd.DataFrame(rows)


def pick(rows: list[dict], classe: str, month: int) -> dict:
    return next(r for r in rows if r["classe"] == classe and r["month"] == month)


def test_a_flat_year_taxes_nothing_but_still_reports_every_month():
    """The markdown renders a "—" row per empty month, so the data must carry
    them; the ledger is what filters down to the months that owe."""
    rows = monthly_apuracao(grid())
    assert len(rows) == 12 * len(CLASSES_APURACAO)
    assert all(r["ir"] == 0.0 and not r["ativo"] for r in rows)


def test_acoes_at_exactly_the_limit_are_isentas():
    """`gross <= 20000`, inclusive — the same boundary `_owed_today` pins on the
    platform side. R$ 20.000,00 exactly is still tax-free."""
    r = pick(monthly_apuracao(grid(**{"4": {"AÇÃO": (20_000, 5_000)}})), "AÇÃO", 4)
    assert r["isento"] is True
    assert r["ir"] == 0.0
    assert r["ganho_isento"] == 5_000.0


def test_one_centavo_over_the_limit_is_taxable_at_15_percent():
    r = pick(monthly_apuracao(grid(**{"4": {"AÇÃO": (20_000.01, 5_000)}})), "AÇÃO", 4)
    assert r["isento"] is False
    assert r["base"] == pytest.approx(5_000.0)
    assert r["aliquota"] == 0.15
    assert r["ir"] == pytest.approx(750.0)


def test_an_isento_gain_does_not_consume_the_accumulated_loss():
    """Best of both worlds: the isento month's gain is free AND the carry it
    could have consumed stays intact for a later taxable month."""
    rows = monthly_apuracao(grid(**{
        "1": {"AÇÃO": (5_000, -10_000)},    # loss, isento month → carry 10k
        "2": {"AÇÃO": (5_000, 4_000)},      # gain, isento month → must not spend it
        "3": {"AÇÃO": (50_000, 10_000)},    # taxable → the full 10k is there to spend
    }))
    assert pick(rows, "AÇÃO", 1)["carry"] == pytest.approx(10_000.0)
    assert pick(rows, "AÇÃO", 2)["compensacao"] == 0.0
    assert pick(rows, "AÇÃO", 2)["carry"] == pytest.approx(10_000.0)
    mar = pick(rows, "AÇÃO", 3)
    assert mar["compensacao"] == pytest.approx(10_000.0)
    assert mar["base"] == 0.0
    assert mar["ir"] == 0.0
    assert mar["carry"] == 0.0


def test_a_loss_in_an_isento_month_still_accumulates():
    r = pick(monthly_apuracao(grid(**{"6": {"AÇÃO": (1_000, -800)}})), "AÇÃO", 6)
    assert r["isento"] is True
    assert r["ganho_isento"] == 0.0
    assert r["carry"] == pytest.approx(800.0)


def test_carry_only_partly_covering_the_gain_leaves_the_rest_taxable():
    rows = monthly_apuracao(grid(**{
        "1": {"AÇÃO": (50_000, -5_000)},
        "2": {"AÇÃO": (50_000, 30_000)},
    }))
    fev = pick(rows, "AÇÃO", 2)
    assert fev["compensacao"] == pytest.approx(5_000.0)
    assert fev["base"] == pytest.approx(25_000.0)
    assert fev["ir"] == pytest.approx(3_750.0)
    assert fev["carry"] == 0.0


def test_fiis_have_no_isencao_and_pay_20_percent():
    r = pick(monthly_apuracao(grid(**{"5": {"FII": (100, 1_000)}})), "FII", 5)
    assert r["isento"] is False
    assert r["aliquota"] == 0.20
    assert r["ir"] == pytest.approx(200.0)


def test_fii_losses_never_offset_acao_gains():
    """Separate carry buckets — the rule the two tables exist to keep apart."""
    rows = monthly_apuracao(grid(**{
        "1": {"FII": (10_000, -8_000)},
        "2": {"AÇÃO": (50_000, 8_000)},
    }))
    assert pick(rows, "AÇÃO", 2)["compensacao"] == 0.0
    assert pick(rows, "AÇÃO", 2)["ir"] == pytest.approx(1_200.0)
    assert pick(rows, "FII", 1)["carry"] == pytest.approx(8_000.0)


def test_bdrs_and_etfs_share_one_line_at_15_percent_with_no_carry():
    """Mirrors the markdown table exactly: BDR and ETF_RV summed, `max(0, pnl*15%)`,
    no compensation tracked. ETF_RF is renda fixa (tributada na fonte) and is not
    apurada here at all."""
    rows = monthly_apuracao(grid(**{
        "7": {"BDR": (10_000, 1_000), "ETF_RV": (5_000, 1_000), "ETF_RF": (9_999, 9_999)},
        "8": {"BDR": (10_000, -4_000)},
        "9": {"BDR": (10_000, 2_000)},
    }))
    jul = pick(rows, "BDR_ETF", 7)
    assert jul["gross"] == pytest.approx(15_000.0)
    assert jul["ir"] == pytest.approx(300.0)
    assert pick(rows, "BDR_ETF", 8)["ir"] == 0.0
    # No carry bucket: August's loss does not shelter September's gain.
    assert pick(rows, "BDR_ETF", 9)["ir"] == pytest.approx(300.0)
    assert "ETF_RF" not in {r["classe"] for r in rows}


def test_a_month_with_activity_is_marked_active_even_when_it_owes_nothing():
    """`ativo` is what tells "no trades at all" from "traded and owed zero" — the
    markdown prints "—" for the first and real numbers for the second, and the
    DARF ledger must not raise an alert over either."""
    rows = monthly_apuracao(grid(**{"2": {"FII": (3_000, -1_000)}}))
    assert pick(rows, "FII", 2)["ativo"] is True
    assert pick(rows, "FII", 2)["ir"] == 0.0
    assert pick(rows, "FII", 1)["ativo"] is False


def test_a_break_even_month_still_counts_as_activity():
    """Sold, netted exactly zero. `ativo` is `gross or pnl`, not `gross and pnl`:
    a break-even sale is a real month that prints real numbers, and the mutation
    suite reaches this line only through a month where one of the two is zero."""
    rows = monthly_apuracao(grid(**{"4": {"AÇÃO": (8_000, 0)}}))
    assert pick(rows, "AÇÃO", 4)["ativo"] is True
    assert pick(rows, "AÇÃO", 4)["ir"] == 0.0


def test_consecutive_isento_losses_accumulate_instead_of_replacing():
    """Two losing isento months in a row: the carry is their *sum*.

    `carry += -pnl`, not `carry = -pnl`. Assignment survives every other test in
    this file, because none of them has two consecutive losses in one bucket — and
    it would under-report the accumulated loss, over-collecting IR in the first
    taxable month that follows."""
    rows = monthly_apuracao(grid(**{
        "1": {"AÇÃO": (5_000, -5_000)},     # isento, feeds the carry
        "2": {"AÇÃO": (5_000, -3_000)},     # isento, feeds it again
        "3": {"AÇÃO": (50_000, 10_000)},    # taxable, spends it
    }))
    assert pick(rows, "AÇÃO", 1)["carry"] == pytest.approx(5_000.0)
    assert pick(rows, "AÇÃO", 2)["carry"] == pytest.approx(8_000.0)
    mar = pick(rows, "AÇÃO", 3)
    assert mar["compensacao"] == pytest.approx(8_000.0)
    assert mar["base"] == pytest.approx(2_000.0)
    # 8.000 of carry, not 3.000: the difference is R$ 750 of IR.
    assert mar["ir"] == pytest.approx(300.0)


def test_consecutive_taxable_losses_accumulate_instead_of_replacing():
    """The same, down the non-isento branch — FIIs never have isenção, so their
    losses reach the carry through the other `carry += -pnl` in the function."""
    rows = monthly_apuracao(grid(**{
        "1": {"FII": (10_000, -5_000)},
        "2": {"FII": (10_000, -3_000)},
        "3": {"FII": (10_000, 10_000)},
    }))
    assert pick(rows, "FII", 2)["carry"] == pytest.approx(8_000.0)
    mar = pick(rows, "FII", 3)
    assert mar["compensacao"] == pytest.approx(8_000.0)
    assert mar["ir"] == pytest.approx(400.0)   # 20% of 2.000


def test_a_gain_of_centavos_is_still_a_gain():
    """The thresholds are `pnl > 0`, not `pnl > 1`: fifty centavos of profit is a
    taxable gain in a taxable month and an isento gain in an isento one. Every other
    test here uses round thousands, which cannot tell the two apart.

    Also the only place that asserts `pnl` is carried through to the row at all —
    the render loops and the DARF ledger both read that key by name."""
    rows = monthly_apuracao(grid(**{
        "5": {"AÇÃO": (8_000, 0.50)},        # isento: gain below the R$20k limit
        "6": {"AÇÃO": (50_000, 0.50)},       # taxable: same gain, over the limit
    }))
    mai = pick(rows, "AÇÃO", 5)
    assert mai["pnl"] == pytest.approx(0.50)
    assert mai["ganho_isento"] == pytest.approx(0.50)
    assert mai["ir"] == 0.0
    jun = pick(rows, "AÇÃO", 6)
    assert jun["pnl"] == pytest.approx(0.50)
    assert jun["base"] == pytest.approx(0.50)
    assert jun["ir"] == pytest.approx(0.075)


# ── Cripto: ganho de capital, R$35k, sem compensação ──────────────────────────
#
# Every assertion below is a rule that differs from ações, and each is the kind a
# well-meaning refactor flattens into "same as ações but another rate".

from scripts.apuracao import (  # noqa: E402
    ALIQUOTA, GCAP_TETO, ISENCAO_LIMIT, REGRAS, annual_apuracao, gcap_aliquota,
)


def cripto_grid(**by_month) -> pd.DataFrame:
    rows = []
    for m in range(1, 13):
        gross, pnl = by_month.get(str(m), (0.0, 0.0))
        rows.append({"month": m, "CRIPTO_gross": float(gross),
                     "CRIPTO_pnl": float(pnl)})
    return pd.DataFrame(rows)


def cripto(rows, month):
    return next(r for r in rows if r["month"] == month)


def test_gcap_bands_are_inclusive_at_the_top():
    assert gcap_aliquota(0.0) == 0.15
    assert gcap_aliquota(5_000_000.0) == 0.15
    assert gcap_aliquota(5_000_000.01) == 0.175
    assert gcap_aliquota(10_000_000.0) == 0.175
    assert gcap_aliquota(10_000_000.01) == 0.20
    assert gcap_aliquota(30_000_000.0) == 0.20
    assert gcap_aliquota(30_000_000.01) == GCAP_TETO
    assert GCAP_TETO == 0.225


def test_cripto_is_isento_up_to_thirty_five_thousand_of_sales():
    """The threshold is on the month's gross alienações, not on the gain, and it
    is R$35k — not the R$20k of ações."""
    rows = monthly_apuracao(cripto_grid(**{"3": (35_000, 9_000)}), ("CRIPTO",))
    r = cripto(rows, 3)
    assert r["isento"] and r["ir"] == 0.0
    assert r["ganho_isento"] == 9_000


def test_cripto_above_the_threshold_pays():
    rows = monthly_apuracao(cripto_grid(**{"3": (35_000.01, 10_000)}), ("CRIPTO",))
    r = cripto(rows, 3)
    assert not r["isento"]
    assert r["base"] == 10_000
    assert r["ir"] == pytest.approx(1_500.0)


def test_a_cripto_loss_is_not_carried_forward():
    """Ganho de capital has no loss compensation. This is the rule most likely to
    be "fixed" into a bug by someone assuming every class works like ações — a
    carried crypto loss would understate the tax owed on the next gain."""
    rows = monthly_apuracao(
        cripto_grid(**{"3": (50_000, -10_000), "4": (50_000, 10_000)}), ("CRIPTO",))
    assert cripto(rows, 3)["carry"] == 0.0
    later = cripto(rows, 4)
    assert later["compensacao"] == 0.0
    assert later["base"] == 10_000
    assert later["ir"] == pytest.approx(1_500.0)


def test_an_isento_cripto_loss_feeds_nothing_either():
    """The ações rule — an isento loss still feeds the carry — must not leak
    across: there is no bucket for it to feed."""
    rows = monthly_apuracao(cripto_grid(**{"3": (10_000, -5_000)}), ("CRIPTO",))
    assert cripto(rows, 3)["carry"] == 0.0


def test_the_cripto_rate_follows_the_size_of_the_gain():
    rows = monthly_apuracao(
        cripto_grid(**{"5": (60_000_000, 6_000_000)}), ("CRIPTO",))
    r = cripto(rows, 5)
    assert r["aliquota"] == 0.175
    assert r["ir"] == pytest.approx(6_000_000 * 0.175)


def test_cripto_carries_its_own_darf_code():
    """4600, not the 6015 of renda variável — a DARF paid under the wrong code is
    a payment the Receita does not match to the debt."""
    rows = monthly_apuracao(cripto_grid(**{"3": (50_000, 1.0)}), ("CRIPTO",))
    assert cripto(rows, 3)["darf"] == "4600"
    assert REGRAS["AÇÃO"].darf == "6015"


def test_cripto_is_not_rendered_by_default():
    """The report is built from B3 extracts, which carry no crypto. Adding it to
    the default render set would put twelve empty months in every declaration."""
    assert "CRIPTO" not in CLASSES_APURACAO
    assert "EXTERIOR" not in CLASSES_APURACAO
    assert "CRIPTO" in REGRAS


# ── Exterior: Lei 14.754/2023, anual, compensável dentro do ano ────────────────

def test_exterior_nets_gains_against_losses_within_the_year():
    """The compensation the law introduced in exchange for ending the deferral:
    losses offset gains across foreign assets inside the same year."""
    out = annual_apuracao({"AAPL": 30_000.0, "MSFT": -10_000.0})
    assert out["base"] == 20_000.0
    assert out["ir"] == pytest.approx(3_000.0)


def test_exterior_is_fifteen_percent_flat():
    out = annual_apuracao({"AAPL": 40_000_000.0})
    assert out["aliquota"] == 0.15, "not the progressive GCAP table"


def test_a_losing_year_abroad_owes_nothing_and_carries_nothing():
    """This function is one year. The law nets inside it; an excess loss does not
    become a carry here, and reporting one would understate next year's tax."""
    out = annual_apuracao({"AAPL": -5_000.0, "MSFT": 1_000.0})
    assert out["base"] == 0.0
    assert out["ir"] == 0.0
    assert out["perdas"] == 5_000.0
    assert out["ganhos"] == 1_000.0


def test_exterior_raises_no_monthly_darf():
    """Lei 14.754 replaced the monthly DARF with one annual apuração settled in
    the ajuste. A code here would tell a user to pay something that does not
    exist."""
    assert annual_apuracao({"AAPL": 1.0})["periodo"] == "anual"
    assert annual_apuracao({"AAPL": 1.0})["darf"] == "-"
    assert REGRAS["EXTERIOR"].periodo == "anual"


def test_an_empty_year_abroad_is_inactive():
    out = annual_apuracao({})
    assert not out["ativo"]
    assert out["ir"] == 0.0


def test_a_zero_result_position_is_neither_gain_nor_loss():
    out = annual_apuracao({"AAPL": 0.0})
    assert out["ganhos"] == 0.0 and out["perdas"] == 0.0
    assert out["ativo"]
    assert out["classe"] == "EXTERIOR"


def test_a_sub_one_real_result_is_still_a_gain():
    """Not a boundary anyone trades at — it is here because "greater than zero"
    and "greater than one" are indistinguishable on every other input, and a rule
    no test can distinguish from a wrong one is a rule nobody can trust."""
    out = annual_apuracao({"AAPL": 0.50, "MSFT": -0.25})
    assert out["ganhos"] == 0.50
    assert out["perdas"] == 0.25
    assert out["base"] == 0.25


def test_a_class_without_isencao_is_never_isento():
    """FII has no isenção at all, which is not the same as an isenção of zero: a
    month with no sales would clear a zero threshold and be reported isento, on
    every one of the twelve empty rows."""
    rows = monthly_apuracao(grid(), ("FII",))
    assert not any(r["isento"] for r in rows)


# ── The back-compat constants other modules still read ────────────────────────

def test_the_legacy_rate_map_still_answers_for_the_rendered_classes():
    assert ALIQUOTA["AÇÃO"] == 0.15
    assert ALIQUOTA["FII"] == 0.20
    assert ALIQUOTA["BDR_ETF"] == 0.15


def test_the_legacy_rate_map_excludes_the_annual_regime():
    """EXTERIOR is not apurado monthly, so a caller iterating ALIQUOTA to build
    month rows must not find it there."""
    assert "EXTERIOR" not in ALIQUOTA
    assert "CRIPTO" in ALIQUOTA


def test_the_isencao_constant_is_the_acoes_threshold():
    assert ISENCAO_LIMIT == 20_000.0
    assert REGRAS["AÇÃO"].isencao == ISENCAO_LIMIT
    assert REGRAS["CRIPTO"].isencao == 35_000.0
