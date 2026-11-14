"""The TLH verdict per bucket, read off monthly_apuracao (scripts/tlh_decisao.py).

The planner used to hand-write the month's tax in four branches over the ações
and FII lines only, so a BDR or ETF de renda variável sale — same operações
comuns carry as ações, 15% with no isenção — was invisible to it: missing from
the month-to-date figures and from every proposed-sell decision.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.tlh_decisao import Status, decidir
from scripts.tlh_plan import harvest, historico, walk_history
from test_tlh_noninteractive import run_tlh

TODAY = pd.Timestamp.today().normalize()
EARLIER = "2020-01-10"
PAST = "2020-03-15"
# A book with no sale at all: one buy, years ago.
NADA = ("ITSA4", EARLIER, "buy", 1, 10.0)


def timeline(*rows) -> pd.DataFrame:
    return pd.DataFrame([{"ticker": t, "date": pd.Timestamp(d), "kind": k, "qty": q,
                          "amount": a} for t, d, k, q, a in rows])


def losers(*rows) -> pd.DataFrame:
    return pd.DataFrame([{"ticker": t, "qty": q, "avg_cost": c, "price": p,
                          "unrealized_pnl": q * (p - c), "max_loss": q * (p - c)}
                         for t, q, c, p in rows])


def test_a_bdr_sold_this_month_is_in_the_operacoes_comuns_month_to_date():
    """Its gain spends the same carry as an ação's. It does not count towards the
    R$ 20k isenção, which reads the ações line alone — so gross stays ações."""
    _carry, mtd = walk_history(timeline(
        ("AAPL34", EARLIER, "buy", 100, 4_000.0),
        ("AAPL34", TODAY, "sell", 100, 5_000.0),
        ("PETR4", EARLIER, "buy", 100, 3_000.0),
        ("PETR4", TODAY, "sell", 50, 2_000.0)))
    assert mtd["AÇÃO"]["pnl"] == pytest.approx(1_000.0 + 500.0)
    assert mtd["AÇÃO"]["gross"] == pytest.approx(2_000.0)


def _decide(tl, vendas, perdedores=None, bucket="AÇÃO"):
    _carry, _mtd, grade = historico(tl)
    return decidir(bucket, grade, vendas,
                   lambda: perdedores if perdedores is not None else losers(), harvest)


def test_an_isento_acao_month_still_owes_on_a_bdr_gain():
    """R$ 5k of ações keeps the ações gain isento, but a BDR gain owes 15% with
    no isenção. The old branches called the whole month isento."""
    d, plan = _decide(timeline(NADA), [
        {"cls": "AÇÃO", "sell_value": 5_000.0, "pnl": 1_000.0},
        {"cls": "BDR", "sell_value": 10_000.0, "pnl": 2_000.0}],
        losers(("VALE3", 100, 60.0, 40.0)))
    assert d["status"] == Status.RECOMENDADO == "TLH recomendado"
    assert d["target_loss"] == pytest.approx(-2_000.0)
    assert [p["ticker"] for p in plan] == ["VALE3"]
    # Harvesting 100 ações at R$ 40 adds R$ 4k of ações gross: still isento.
    assert d["final_gross"] == pytest.approx(9_000.0)
    # And the IR it reports is the apuração's, not the greedy's hope: the ações
    # line nets its own R$ 1k isento gain first, so only R$ 1k of the harvested
    # R$ 2k reaches the pool, and R$ 1k of BDR gain still owes 15%.
    assert d["final_pnl"] == pytest.approx(1_000.0)
    assert d["ir"] == pytest.approx(150.0)
    # No carry was brought into the month, so none was used and none is left.
    assert (d["carry_used"], d["carry_after"]) == (0.0, 0.0)


def test_a_bdr_loss_shelters_an_acao_gain_in_the_same_month():
    d, plan = _decide(timeline(NADA), [
        {"cls": "AÇÃO", "sell_value": 30_000.0, "pnl": 3_000.0},
        {"cls": "ETF_RV", "sell_value": 10_000.0, "pnl": -3_000.0}])
    assert (d["status"], d["ir"], plan) == (Status.NENHUM, 0.0, [])
    assert d["pnl_after"] == pytest.approx(0.0)


def test_a_bucket_with_nothing_this_month_and_nothing_proposed_is_not_planned():
    assert _decide(timeline(NADA), [], bucket="FII") == (None, [])


def test_no_candidates_reports_the_tax_the_apuracao_computes():
    d, plan = _decide(timeline(("VALE3", EARLIER, "buy", 100, 10_000.0),
                               ("VALE3", PAST, "sell", 100, 8_000.0)),
                      [{"cls": "AÇÃO", "sell_value": 30_000.0, "pnl": 5_000.0}])
    assert d["status"] == Status.SEM_CANDIDATOS and plan == []
    assert d["carry_used"] == pytest.approx(2_000.0)
    assert d["ir"] == pytest.approx(3_000.0 * 0.15)
    assert f"{d['status']}" == "TLH desejável mas sem candidatos"


def test_a_proposed_etf_sale_lands_in_the_acoes_bucket_of_the_json():
    """IVVB11 is an ETF de renda variável: the old planner put its sale in no
    bucket at all and reported nothing to decide."""
    r = run_tlh(["--json", "--sells", "IVVB11:45"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    (sell,) = result["sells"]
    assert sell["cls"] == "ETF_RV"
    b = result["buckets"]["AÇÃO"]
    assert b["pnl_after"] == pytest.approx(result["situation"]["AÇÃO"]["pnl_mtd"] + sell["pnl"])
    assert b["status"] in {s.value for s in Status}


def test_overharvesting_shows_up_as_carry_gained_not_as_carry_spent():
    """carry_used only ever counts the carry a plan consumes. A plan that harvests
    past zero *adds* to the carryforward, and carry_after is the field that says
    so. One share of a loser at R$ 5.000 a share is the least harvest can sell
    against a R$ 2.000 target."""
    d, plan = _decide(timeline(("VALE3", EARLIER, "buy", 100, 10_000.0),
                               ("VALE3", PAST, "sell", 100, 9_000.0)),
                      [{"cls": "AÇÃO", "sell_value": 30_000.0, "pnl": 3_000.0}],
                      losers(("ABCD3", 2, 5_500.0, 500.0)))
    assert d["target_loss"] == pytest.approx(-2_000.0)
    assert [p["qty"] for p in plan] == [1.0]
    assert d["final_pnl"] == pytest.approx(-2_000.0)
    assert (d["carry_used"], d["carry_after"], d["ir"]) == (0.0, pytest.approx(3_000.0), 0.0)


def test_an_isento_gain_beside_a_bdr_loss_spends_no_carry():
    """The isento ações gain does not offset the BDR loss — it is tax-free and
    leaves the pool alone — so the BDR loss is added in full, and no carry is
    "used" even though the bucket's net result is positive."""
    d, _plan = _decide(timeline(NADA), [
        {"cls": "AÇÃO", "sell_value": 5_000.0, "pnl": 1_000.0},
        {"cls": "BDR", "sell_value": 5_000.0, "pnl": -500.0}])
    assert d["status"] == Status.NENHUM and "isento" in d["rationale"]
    assert d["pnl_after"] == pytest.approx(500.0)
    assert (d["carry_used"], d["carry_after"]) == (0.0, pytest.approx(500.0))


def test_a_harvest_that_would_cost_the_isencao_is_never_recommended():
    """The BDR gain is what owes here; the ações line is isento at R$15k gross.
    Selling VALE3 to harvest its loss adds R$11k of ações gross — past R$20k —
    and the R$3k ações gain loses its isenção: IR goes from R$150 to R$450. A
    plan that raises the bill is not a plan; say there is no candidate."""
    d, plan = _decide(timeline(NADA), [
        {"cls": "AÇÃO", "sell_value": 15_000.0, "pnl": 3_000.0},
        {"cls": "BDR", "sell_value": 5_000.0, "pnl": 1_000.0}],
        losers(("VALE3", 400, 30.0, 27.5)))
    assert d["status"] is Status.SEM_CANDIDATOS
    assert plan == []
    assert d["ir"] == pytest.approx(150.0)
    assert d["final_gross"] == pytest.approx(15_000.0)
    assert "isenção" in d["rationale"]


def test_an_isento_month_harvests_only_what_fits_under_the_limit():
    """R$1k of BDR gain owes R$150; the R$3k ações gain is isento at R$15k gross.
    Aimed at the R$1k base alone, the greedy sells 13 shares (R$975 of loss) and
    the ações line just nets them against its own isento gain: IR unchanged. So
    the retry aims at base + isento gain (R$4k) over what fits in the R$5k of
    room: 53 shares, R$3,975 of loss, R$1,325 of gross. The ações line ends at
    −R$975, which shelters all but R$25 of the BDR gain, and stays isento."""
    d, plan = _decide(timeline(NADA), [
        {"cls": "AÇÃO", "sell_value": 15_000.0, "pnl": 3_000.0},
        {"cls": "BDR", "sell_value": 5_000.0, "pnl": 1_000.0}],
        losers(("VALE3", 200, 100.0, 25.0)))
    assert d["status"] is Status.RECOMENDADO
    assert [(p["ticker"], p["qty"]) for p in plan] == [("VALE3", 53.0)]
    assert d["final_gross"] == pytest.approx(15_000.0 + 53 * 25.0)
    assert d["final_gross"] <= 20_000.0
    assert d["ir"] == pytest.approx(25.0 * 0.15)
