"""One label table and one contract walk for renda fixa (scripts/renda_fixa.py).

load_b3 (trades.csv, positions.csv) and irpf_2026.renda_fixa_year_end (Bens e
Direitos) used to classify the same Movimentação rows with two tables "kept in
step" by comment, and to price a resgate with no Quantidade two ways. These pin
the shared table and the rule that gives such a row its units.
"""
from __future__ import annotations

import pandas as pd
import pytest

from scripts import renda_fixa
from scripts.load_b3 import derive_positions, parse_movimentacao
from scripts.renda_fixa import Contrato, preencher_quantidades, rf_kind
from test_import_rules import MOV_COLS, mov, run_main, write  # noqa: F401  (fixture)


@pytest.mark.parametrize("label, credito, has_value, kind", [
    ("APLICAÇÃO", True, True, "aplic"),
    ("APLICAÇÃO/DEPÓSITO", True, True, "aplic"),
    ("Compra", True, True, "aplic"),
    ("VENCIMENTO", False, True, "resgate"),
    ("RESGATE ANTECIPADO", False, True, "resgate"),
    ("Resgate", False, True, "resgate"),
    ("Venda", False, True, "resgate"),
    ("COMPRA / VENDA", True, True, "aplic"),
    ("COMPRA / VENDA", False, True, "resgate"),
    ("Transferência - Liquidação", False, True, "resgate"),
    ("RETIRADA DE CUSTÓDIA", False, True, "resgate"),
    ("RETIRADA DE CUSTÓDIA", False, False, "frozen"),
    ("PAGAMENTO DE JUROS", True, True, "juros"),
    ("JUROS", True, True, "juros"),
    ("AMORTIZAÇÃO", True, True, "amort"),
    ("Amortização", True, True, "amort"),
    ("Rendimento", True, True, "other"),
])
def test_rf_kind(label, credito, has_value, kind):
    assert rf_kind(label, credito, has_value) == kind


CRI = "CRI - 24L0000031 - OPEA SECURITIZADORA S/A"


def test_a_juros_row_on_a_cri_is_booked_not_named_as_unknown(tmp_path):
    """irpf_2026 has always read "JUROS" as a coupon; load_b3 had no rule for it
    and listed every one in movimentacao_review as an unknown label."""
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Credito", "15/05/2025", "JUROS", CRI, 8, 12.05, 96.40)])
    trades, ev, review = parse_movimentacao(path)
    assert review == [] and trades.empty
    assert ev[["event_type", "event_category", "amount"]].values.tolist() == [
        ["JUROS", "other", 96.40]]


def test_a_juros_label_on_an_equity_is_still_named(tmp_path):
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Credito", "15/05/2025", "JUROS", "PETR4 - PETROBRAS", 8, 1.0, 8.0)])
    (review,) = parse_movimentacao(path)[2]
    assert review["label"] == "JUROS"


def test_a_resgate_with_no_quantity_in_another_extract_still_takes_its_share(run_main):
    """Movimentação is downloaded by period, so the aplicação and the resgate of
    one CDB routinely sit in two files. The units of the resgate depend on the
    contract's cost, which only the whole book has."""
    k4 = "CDB - K4 - BANCO X S/A"
    summary, _err, trades, _ev, pos = run_main({
        "movimentacao-2024.xlsx": (MOV_COLS, [
            mov("Credito", "01/06/2024", "APLICAÇÃO", k4, "-", "-", 10_000.0)]),
        "movimentacao-2025.xlsx": (MOV_COLS, [
            mov("Debito", "01/03/2025", "RESGATE ANTECIPADO", k4, "-", "-", 5_200.0)]),
    })
    assert trades["qty"].tolist() == [1.0, pytest.approx(0.52)]
    assert trades["price"].tolist() == [10_000.0, pytest.approx(10_000.0)]
    assert trades["signed_qty"].tolist() == [1.0, pytest.approx(-0.52)]
    assert pos["total_cost"].tolist() == [pytest.approx(4_800.0)]
    assert summary["incomplete_history"] == []


def _trades(*rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "side": s, "ticker": "CDB - X",
                          "market": "Renda Fixa",
                          "qty": q, "price": None if q is None else a / q, "amount": a,
                          "signed_qty": None} for d, s, q, a in rows])


def test_a_book_with_every_quantity_is_left_alone():
    t = _trades(("2025-01-02", "Compra", 100.0, 1_000.0))
    assert preencher_quantidades(t) is t
    empty = pd.DataFrame()  # load_b3 with no trade-bearing extract at all
    assert preencher_quantidades(empty) is empty


def test_a_resgate_of_a_contract_with_no_visible_cost_stays_one_unit_and_is_flagged():
    t = preencher_quantidades(_trades(("2025-03-01", "Venda", None, 5_000.0)))
    assert t["qty"].tolist() == [1.0] and t["signed_qty"].tolist() == [-1.0]
    pos, incomplete = derive_positions(t)
    assert pos.empty and incomplete == ["CDB - X"]


def test_units_of_a_contract_without_quantities_override_a_resgate_that_has_some():
    """Aplicações with no Quantidade put the contract in its own units; a resgate
    that carries B3's units would sell 2.000.000 of them out of 1."""
    t = preencher_quantidades(_trades(("2025-01-02", "Compra", None, 20_000.0),
                                      ("2025-02-03", "Compra", None, 10_000.0),
                                      ("2025-06-02", "Venda", 2_000_000.0, 15_000.0)))
    assert t["qty"].tolist() == [1.0, pytest.approx(0.5), pytest.approx(0.75)]
    pos, incomplete = derive_positions(t)
    assert pos["total_cost"].tolist() == [pytest.approx(15_000.0)] and incomplete == []


def test_contrato_frozen_juros_and_an_amortizacao_past_the_cost():
    c = Contrato(por_quantidade=True)
    c.lancar(renda_fixa.APLIC, 1_000.0, 10.0, 2025)
    c.lancar(renda_fixa.JUROS, 50.0, 0.0, 2025)
    c.lancar(renda_fixa.FROZEN, 0.0, 10.0, 2026)
    assert (c.frozen, c.juros[2025], c.cost, c.incomplete) == (True, 50.0, 1_000.0, False)
    assert c.lancar(renda_fixa.OTHER, 99.0, 1.0, 2026) == 0.0
    c.lancar(renda_fixa.AMORT, 1_200.0, 0.0, 2026)
    assert (c.cost, c.incomplete) == (0.0, True)
    # Once incomplete, no rendimento: the gain is a missing number, not a profit.
    assert c.lancar(renda_fixa.RESGATE, 500.0, 10.0, 2026) == pytest.approx(10.0)
    assert c.rendimento[2026] == 0.0
