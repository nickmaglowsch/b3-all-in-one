"""Self-checks for renda_fixa_year_end's balance walk and rendimentos.

Bens e Direitos must report the *cost* of what is still held on each 31/12, and
must agree with positions.csv on the same contract — the two are the same
accounting seen from two surfaces. The rendimentos it can support differ sharply
by código, and the report has to say which is which rather than round them
together.
"""
import shutil

import pandas as pd
import pytest

from scripts import irpf_2026
from scripts.irpf_2026 import renda_fixa_year_end

COLUMNS = ["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição",
           "Quantidade", "Preço unitário", "Valor da Operação"]


@pytest.fixture
def book(tmp_path, monkeypatch):
    """Write a movimentação extract into an isolated ROOT and read it back.

    renda_fixa_year_end globs `ROOT/b3_imports`, so the fixture repoints ROOT
    rather than reaching into the vendored one.
    """
    imports = tmp_path / "b3_imports"
    imports.mkdir()
    monkeypatch.setattr(irpf_2026, "ROOT", tmp_path)

    def run(rows, year=2025):
        for f in imports.glob("*.xlsx"):
            f.unlink()
        pd.DataFrame(rows, columns=COLUMNS).to_excel(
            imports / "movimentacao-test.xlsx", index=False)
        return renda_fixa_year_end(year)

    yield run
    shutil.rmtree(tmp_path, ignore_errors=True)


def only(df):
    assert len(df) == 1, f"expected one contract, got {len(df)}"
    return df.iloc[0]


def test_an_aplicacao_after_a_year_end_is_not_counted_into_it(book):
    """Two aplicações into one contract, straddling 31/12/2024. The 2024 balance
    is the first alone — reporting the contract's final size as its opening
    balance overstates last year's declaration by the whole second tranche."""
    r = only(book([
        ["Credito", "01/06/2024", "APLICAÇÃO", "CDB - A - BANCO X S/A",
         "XP", 1_000_000, 0.01, 10_000.00],
        ["Credito", "01/06/2025", "APLICAÇÃO", "CDB - A - BANCO X S/A",
         "XP", 1_000_000, 0.01, 10_000.00],
    ]))
    assert r["value_prev"] == pytest.approx(10_000.00)
    assert r["value_cur"] == pytest.approx(20_000.00)


def test_a_partial_resgate_leaves_the_rest_declared(book):
    """Half the contract redeemed is half the contract still owned. Treating the
    first resgate as terminal wrote the surviving half out of Bens e Direitos."""
    r = only(book([
        ["Credito", "01/06/2024", "APLICAÇÃO", "CDB - B - BANCO X S/A",
         "XP", 2_000_000, 0.01, 20_000.00],
        ["Debito", "01/06/2025", "RESGATE ANTECIPADO", "CDB - B - BANCO X S/A",
         "XP", 1_000_000, 0.011, 11_000.00],
    ]))
    assert r["value_prev"] == pytest.approx(20_000.00)
    assert r["value_cur"] == pytest.approx(10_000.00)   # half the cost remains
    # Redeemed 11,000 against 10,000 of cost: a 1,000 gain, net of IR retido.
    assert r["rendimento_cur"] == pytest.approx(1_000.00)


def test_an_amortizacao_reduces_the_declared_cost(book):
    """A CRI returns principal in instalments. The cost has to fall with it, or
    Bens e Direitos declares money the holder has already been paid back — and
    disagrees with the same contract's row in positions.csv."""
    r = only(book([
        ["Credito", "05/12/2024", "COMPRA / VENDA", "CRI - C - OPEA S/A",
         "XP", 8, 1035.42, 8283.36],
        ["Credito", "15/05/2025", "AMORTIZAÇÃO", "CRI - C - OPEA S/A",
         "XP", 8, 51.59, 412.75],
    ]))
    assert r["value_prev"] == pytest.approx(8283.36)
    assert r["value_cur"] == pytest.approx(7870.61)
    assert r["rendimento_cur"] == 0.0        # principal back is not income


def test_coupons_are_income_and_amortizacao_is_not(book):
    """PAGAMENTO DE JUROS on isento paper is declarable in full under código 12,
    and was rendered nowhere before. AMORTIZAÇÃO alongside it must not join it."""
    r = only(book([
        ["Credito", "05/12/2024", "COMPRA / VENDA", "CRI - D - OPEA S/A",
         "XP", 8, 1035.42, 8283.36],
        ["Credito", "15/05/2025", "AMORTIZAÇÃO", "CRI - D - OPEA S/A",
         "XP", 8, 51.59, 412.75],
        ["Credito", "15/05/2025", "PAGAMENTO DE JUROS", "CRI - D - OPEA S/A",
         "XP", 8, 12.05, 96.40],
    ]))
    assert r["juros_cur"] == pytest.approx(96.40)
    assert r["irpf_codigo"] == "03"          # isento → código 12 in the report


def test_a_contract_opened_and_closed_inside_the_year_still_reports_its_gain(book):
    """It holds nothing on either 31/12, so it has no Bens e Direitos row — but
    the gain is declarable and dropping the contract loses it silently."""
    r = only(book([
        ["Credito", "01/02/2025", "APLICAÇÃO", "CDB - E - BANCO X S/A",
         "XP", 1_000_000, 0.01, 10_000.00],
        ["Debito", "01/11/2025", "VENCIMENTO", "CDB - E - BANCO X S/A",
         "XP", 1_000_000, 0.011, 11_000.00],
    ]))
    assert r["value_prev"] == 0.0 and r["value_cur"] == 0.0
    assert r["rendimento_cur"] == pytest.approx(1_000.00)


def test_a_frozen_custody_stays_declared_at_cost(book):
    """A withdrawal that never settled paid the holder nothing, so the paper is
    still owned. The report flags it as under liquidação — a zero balance under
    that flag would contradict the flag."""
    r = only(book([
        ["Credito", "20/01/2024", "APLICAÇÃO", "CDB - F - BANCO SINCERO S/A",
         "XP", 950_000, 0.01, 9_500.00],
        ["Debito", "15/01/2025", "RETIRADA DE CUSTÓDIA",
         "CDB - F - BANCO SINCERO S/A", "XP", 950_000, 0, 0],
    ]))
    assert bool(r["is_frozen"]) is True
    assert r["value_cur"] == pytest.approx(9_500.00)
    assert r["rendimento_cur"] == 0.0        # nothing was ever credited


def test_tesouro_direto_is_picked_up_despite_its_mixed_case_name(book):
    """B3 caps the bank paper and writes the Tesouro titles mixed-case, so a
    case-sensitive match dropped every Tesouro row from the report."""
    r = only(book([
        ["Credito", "10/03/2024", "Compra", "Tesouro Selic 2029",
         "XP", 0.15, 14_000.0, 2_100.00],
    ]))
    assert r["code"] == "Tesouro Selic 2029"
    assert r["irpf_codigo"] == "02"          # Tesouro is tributado
    assert r["value_cur"] == pytest.approx(2_100.00)
