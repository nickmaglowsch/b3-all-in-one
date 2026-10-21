"""The preço médio walk (scripts/realizacao.py) and the ticker classes (ativos_b3).

Each test here is one of the taxcore audit's repro cases (verify1.py), stated on
a timeline built line by line: prior-year carry (H1), ETFs and units read as FII
(H3), day trade (H4), selling more than is held (M1), the IRRF estimate (M3).
"""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.ativos_b3 import asset_class, fii_presumido, irpf_classification
from scripts.realizacao import (
    apurar, grade_mensal, irrf_mensal, realizar, realized_per_month, snapshot_positions,
)
from scripts.relatorio_rv import payload_json, render_secao3


def tl(*rows, broker="XP"):
    """(ticker, date, kind, qty, amount[, broker]) → a build_timeline frame, in the
    order given — so a test can put a same-day sell before its buy."""
    out = []
    for r in rows:
        t, d, k, q, a = r[:5]
        out.append({"ticker": t, "date": pd.Timestamp(d), "kind": k, "qty": float(q),
                    "amount": float(a), "broker": r[5] if len(r) > 5 else broker})
    return pd.DataFrame(out)


def linha(rv, classe, month):
    return next(r for r in rv["apuracao"] if r["classe"] == classe and r["month"] == month)


# ── H3: classification ────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker, cls", [
    ("BOVA11", "ETF_RV"), ("SMAL11", "ETF_RV"), ("HASH11", "ETF_RV"), ("GOLD11", "ETF_RV"),
    ("IVVB11", "ETF_RV"), ("LFTB11", "ETF_RF"), ("IMAB11", "ETF_RF"),
    ("XFIX11", "FII"), ("MXRF11", "FII"),
    ("BRBI11", "AÇÃO"), ("IGTI11", "AÇÃO"), ("CPLE11", "AÇÃO"), ("TAEE11", "AÇÃO"),
    ("AAPL34", "BDR"), ("ROXO34", "BDR"), ("BIVB39", "BDR"), ("XPBR31", "BDR"),
    ("AMZO33", "BDR"), ("PETR4", "AÇÃO"), ("CDB - CDB24C7HQ2L", "RF"),
])
def test_asset_class(ticker, cls):
    assert asset_class(ticker) == cls
    assert asset_class(ticker.lower()) == cls


def test_an_unknown_11_is_still_a_fii_but_flagged_as_presumed():
    assert asset_class("ZZZZ11") == "FII"
    assert fii_presumido("ZZZZ11")
    assert not fii_presumido("HGLG11")     # a FII known by name
    assert not fii_presumido("XFIX11")     # the ETF of FIIs, by name
    assert not fii_presumido("BOVA11")     # an ETF
    assert "presumido" in irpf_classification("ZZZZ11")[2]


@pytest.mark.parametrize("ticker, expected", [
    ("IVVB11", ("07", "09", "Cotas de Fundo de Índice (ETF) - IVVB11 (iShares S&P 500)")),
    ("XFIX11", ("07", "09", "Cotas de Fundo de Índice (ETF) - XFIX11 (Trend IFIX)")),
    ("LFTB11", ("04", "02", "Cotas de ETF de Renda Fixa - LFTB11 (Tesouro Selic)")),
    ("HGLG11", ("07", "03", "Cotas de Fundo de Investimento Imobiliário (FII)")),
    ("BIVB39", ("03", "09", "BDR — Brazilian Depositary Receipt")),
    ("KLBN11", ("03", "01", "Ações (Unit)")),
    ("VALE3", ("03", "01", "Ações")),
])
def test_bens_e_direitos_codes(ticker, expected):
    assert irpf_classification(ticker) == expected


# ── H1: prior-year loss ───────────────────────────────────────────────────────

def test_a_prior_year_loss_offsets_this_years_gain():
    rv = apurar(tl(("PETR4", "2024-03-01", "buy", 10_000, 400_000),
                   ("PETR4", "2024-06-01", "sell", 10_000, 300_000),
                   ("VALE3", "2025-01-10", "buy", 1_000, 50_000),
                   ("VALE3", "2025-02-10", "sell", 1_000, 100_000)), 2025, asset_class)
    fev = linha(rv, "AÇÃO", 2)
    assert fev["compensacao"] == pytest.approx(50_000.0)
    assert fev["ir"] == 0.0
    assert rv["carry_inicial"]["COMUNS"] == pytest.approx(100_000.0)
    assert len(rv["apuracao"]) == 12 * 4 and {r["year"] for r in rv["apuracao"]} == {2025}
    assert rv["darf"][1]["darf"] == 0.0


# ── H4: day trade ─────────────────────────────────────────────────────────────

def test_a_same_day_sell_listed_before_its_buy_is_a_day_trade_not_dropped():
    t = tl(("PETR4", "2025-07-01", "sell", 100, 3_100), ("PETR4", "2025-07-01", "buy", 100, 3_000))
    rv = apurar(t, 2025, asset_class)
    assert linha(rv, "DAYTRADE", 7)["ir"] == pytest.approx(20.0)
    assert linha(rv, "AÇÃO", 7)["ativo"] is False
    assert snapshot_positions(t, pd.Timestamp("2025-12-31")).empty


def test_a_day_trade_leaves_the_swing_preco_medio_alone():
    """verify1 case 4c: 100 held at 20; same day buy 100 @ 40 and sell 100 @ 41.
    The old walk booked R$ 1.100 of swing gain at a blended PM of 30."""
    t = tl(("PETR4", "2025-01-01", "buy", 100, 2_000),
           ("PETR4", "2025-07-01", "buy", 100, 4_000),
           ("PETR4", "2025-07-01", "sell", 100, 4_100))
    r = realizar(t)
    assert r["daytrade"].tolist() == [True]
    assert r["pnl"].tolist() == [pytest.approx(100.0)]
    snap = snapshot_positions(t, pd.Timestamp("2025-12-31"))
    assert snap.iloc[0]["qty"] == 100 and snap.iloc[0]["total_cost"] == pytest.approx(2_000.0)


def test_a_partial_day_trade_sends_the_rest_through_the_swing_walk():
    t = tl(("PETR4", "2025-01-01", "buy", 100, 2_000),
           ("PETR4", "2025-07-01", "buy", 200, 8_000),
           ("PETR4", "2025-07-01", "sell", 100, 4_100),
           ("PETR4", "2025-08-01", "sell", 300, 15_000))
    r = realizar(t)
    dt, swing = r[r["daytrade"]], r[~r["daytrade"]]
    assert dt["qty"].sum() == 100 and dt["pnl"].sum() == pytest.approx(100.0)
    # Swing: 100 @ 20 + 100 @ 40 → PM 30; 300 sold @ 50 → only 200 covered.
    assert swing["avg_cost"].iloc[0] == pytest.approx(30.0)
    assert swing["pnl"].iloc[0] == pytest.approx(4_000.0)


def test_buys_and_sells_at_different_brokers_are_not_a_day_trade():
    """Lei 9.959/2000, art. 8º, § 1º: the same intermediary."""
    t = tl(("PETR4", "2025-01-01", "buy", 100, 2_000, "A"),
           ("PETR4", "2025-07-01", "sell", 100, 4_100, "B"),
           ("PETR4", "2025-07-01", "buy", 100, 4_000, "A"))
    r = realizar(t)
    assert not r["daytrade"].any()
    # Buys first within the day: PM 30 on the sale.
    assert r["pnl"].iloc[0] == pytest.approx(1_100.0)


def test_a_fii_day_trade_stays_on_the_fii_line():
    rv = apurar(tl(("HGLG11", "2025-03-03", "buy", 10, 1_500),
                   ("HGLG11", "2025-03-03", "sell", 10, 1_600)), 2025, asset_class)
    assert linha(rv, "FII", 3)["ir"] == pytest.approx(20.0)
    assert linha(rv, "DAYTRADE", 3)["ativo"] is False


# ── M1: selling more than is held ─────────────────────────────────────────────

def test_an_oversell_keeps_its_full_value_in_the_gross_and_is_flagged():
    """verify1 case 3: 100 held, 1.000 sold for R$ 35.000. The old walk counted
    R$ 3.000 of gross — an isento month — and said nothing."""
    t = tl(("PETR4", "2025-05-01", "buy", 100, 3_000), ("PETR4", "2025-05-02", "sell", 1_000, 35_000))
    rv = apurar(t, 2025, asset_class)
    mai = linha(rv, "AÇÃO", 5)
    assert mai["gross"] == pytest.approx(35_000.0)
    assert mai["isento"] is False
    assert mai["pnl"] == pytest.approx(500.0)     # 100 covered @ 35 vs PM 30
    [d] = payload_json(2025, rv, t)["avisos"]["descoberto"]
    assert d == {"date": "2025-05-02", "ticker": "PETR4", "qty": 900.0, "valor": 31_500.0}


def test_a_sale_of_nothing_books_no_result_and_a_later_buy_opens_a_fresh_position():
    t = tl(("PETR4", "2025-05-02", "sell", 1_000, 30_000), ("PETR4", "2025-05-20", "buy", 1_000, 28_000))
    r = realizar(t)
    assert r["pnl"].tolist() == [0.0] and r["descoberto"].tolist() == [1_000.0]
    snap = snapshot_positions(t, pd.Timestamp("2025-12-31"))
    assert snap.iloc[0]["qty"] == 1_000 and snap.iloc[0]["total_cost"] == pytest.approx(28_000.0)


# ── M3: IRRF estimate ─────────────────────────────────────────────────────────

def test_irrf_swing_is_waived_up_to_one_real_a_month_per_broker():
    r = realizar(tl(("VALE3", "2025-01-02", "buy", 1_000, 10_000),
                    ("VALE3", "2025-02-03", "sell", 100, 20_000),      # 0,005% = R$ 1,00
                    ("VALE3", "2025-03-03", "sell", 100, 15_000, "A"),
                    ("VALE3", "2025-03-04", "sell", 100, 15_000, "A"),  # A: R$ 1,50
                    ("VALE3", "2025-03-05", "sell", 100, 15_000, "B"),  # B alone: R$ 0,75
                    ("LFTB11", "2025-03-01", "buy", 10, 150_000),
                    ("LFTB11", "2025-03-02", "sell", 10, 160_000)))     # renda fixa: none
    irrf = irrf_mensal(r, asset_class)
    assert (2025, 2) not in irrf
    assert irrf[(2025, 3)] == {"AÇÃO": pytest.approx(1.50)}


def test_irrf_day_trade_is_one_percent_of_the_days_gain():
    r = realizar(tl(("PETR4", "2025-07-01", "buy", 100, 3_000), ("PETR4", "2025-07-01", "sell", 100, 3_100),
                    ("VALE3", "2025-07-01", "buy", 10, 600), ("VALE3", "2025-07-01", "sell", 10, 580),
                    ("BOVA11", "2025-07-02", "buy", 10, 1_000), ("BOVA11", "2025-07-02", "sell", 10, 900)))
    # 1 Jul nets +100 −20 = +80 → R$ 0,80; 2 Jul is a loss → nothing.
    assert irrf_mensal(r, asset_class) == {(2025, 7): {"DAYTRADE": pytest.approx(0.80)}}


def test_irrf_of_no_sales_is_empty():
    assert irrf_mensal(realizar(tl(("VALE3", "2025-01-02", "buy", 1, 10))), asset_class) == {}


def test_bonus_shares_carry_their_custo_atribuido_through_the_shared_walk():
    """verify1 case 5, on the walk the report and TLH now share: the import side
    puts qty × price of a bonificação in `amount` (Lei 9.249/95, art. 10)."""
    t = pd.concat([tl(("GOAU4", "2025-01-01", "buy", 100, 1_000)),
                   pd.DataFrame([{"ticker": "GOAU4", "date": pd.Timestamp("2025-06-10"),
                                  "kind": "qty_event", "qty": 100.0, "amount": 890.0,
                                  "broker": "XP"}]),
                   tl(("GOAU4", "2025-08-01", "sell", 200, 2_000))], ignore_index=True)
    assert realizar(t)["pnl"].sum() == pytest.approx(110.0)


# ── the frames ────────────────────────────────────────────────────────────────

def test_a_book_with_no_sale_still_gets_twelve_months():
    g = grade_mensal(realizar(tl(("VALE3", "2025-01-02", "buy", 1, 10))), asset_class, 2025)
    assert g["year"].tolist() == [2025] * 12 and g["month"].tolist() == list(range(1, 13))
    assert g.filter(like="_gross").to_numpy().sum() == 0


def test_realized_per_month_keeps_only_the_year():
    t = tl(("VALE3", "2024-01-02", "buy", 10, 100), ("VALE3", "2024-05-02", "sell", 5, 60),
           ("VALE3", "2025-05-02", "sell", 5, 70))
    assert realized_per_month(t, 2025)["sell_value"].tolist() == [70.0]


def test_the_snapshot_broker_is_the_last_real_buy_and_events_move_the_position():
    t = pd.concat([
        tl(("WEGE3", "2025-01-02", "buy", 100, 3_000, "XP"),
           ("WEGE3", "2025-02-02", "buy", 100, 3_000, "override")),
        pd.DataFrame([{"ticker": "WEGE3", "date": pd.Timestamp("2025-03-01"), "kind": "qty_event",
                       "qty": 200.0, "amount": 0.0, "broker": "XP"},
                      {"ticker": "WEGE3", "date": pd.Timestamp("2025-03-02"), "kind": "amort",
                       "qty": 0.0, "amount": 500.0, "broker": None}])], ignore_index=True)
    snap = snapshot_positions(t, pd.Timestamp("2025-12-31")).iloc[0]
    assert snap["broker"] == "XP"
    assert snap["qty"] == 400 and snap["total_cost"] == pytest.approx(5_500.0)


# ── the rendered section ──────────────────────────────────────────────────────

def test_section_three_renders_every_warning_and_the_darf_table():
    t = tl(("PETR4", "2024-01-02", "buy", 100, 5_000), ("PETR4", "2024-02-02", "sell", 100, 4_000),
           ("ZZZZ11", "2025-01-02", "buy", 10, 1_000),
           ("PETR4", "2025-07-01", "buy", 100, 3_000), ("PETR4", "2025-07-01", "sell", 100, 3_040),
           ("PETR4", "2025-08-01", "buy", 100, 3_000), ("PETR4", "2025-08-01", "sell", 100, 3_040),
           ("VALE3", "2025-09-01", "sell", 10, 900))
    rv = apurar(t, 2025, asset_class)
    by_class = {}
    for r in rv["apuracao"]:
        by_class.setdefault(r["classe"], []).append(r)
    out: list[str] = []
    render_secao3(out.append, 2025, rv, t, by_class, 0.0)
    md = "\n".join(out)
    assert "Prejuízo acumulado trazido de anos anteriores" in md and "COMUNS R$ 1,000.00" in md
    assert "Vendas acima da posição registrada" in md and "| 2025-09-01 | VALE3 | 10 |" in md
    assert "Classificação presumida**: ZZZZ11" in md
    assert "Tabela mensal 2025 — Day trade" in md
    # July: R$ 8 of day-trade IR less R$ 0,40 of IRRF (1% of R$ 40) is below
    # R$ 10 and rolls into August, which pays both months.
    assert "| Jul | R$ 8.00 | R$ 0.40 | R$ 0.00 | — (R$ 7.60 → mês seguinte) |" in md
    assert "| Ago | R$ 8.00 | R$ 0.40 | R$ 7.60 | R$ 15.20 |" in md


def test_section_three_says_so_when_no_darf_is_due():
    t = tl(("VALE3", "2025-01-02", "buy", 1, 10))
    rv = apurar(t, 2025, asset_class)
    by_class = {}
    for r in rv["apuracao"]:
        by_class.setdefault(r["classe"], []).append(r)
    out: list[str] = []
    render_secao3(out.append, 2025, rv, t, by_class, 0.0)
    assert "Nenhum DARF devido em 2025." in out
