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


# --- the two surfaces must agree -------------------------------------------

def test_bens_e_direitos_matches_positions_csv_for_the_same_contract(book, tmp_path):
    """The claim in this module's docstring, actually asserted.

    positions.csv (load_b3.derive_positions) and Bens e Direitos
    (renda_fixa_year_end) are the same accounting seen from two surfaces, and
    they read the same extract by two different paths — one through the parsed
    CSVs, one straight off the .xlsx. Nothing compared them, and two separate
    bugs lived in that gap: a bare F-strip on cash-event tickers detached the
    amortização from its contract in one path only, and an unclamped
    subtraction let the other print a negative custo de aquisição.
    """
    from scripts.load_b3 import derive_positions, read_movimentacao

    rows = [
        # An F-terminated contract code, so the fractional strip is in the path.
        ["Credito", "05/12/2024", "COMPRA / VENDA", "CRI - 24L000003F - OPEA S/A",
         "XP", 8, 1035.42, 8283.36],
        ["Credito", "15/05/2025", "AMORTIZAÇÃO", "CRI - 24L000003F - OPEA S/A",
         "XP", 8, 51.59, 412.75],
        ["Credito", "20/01/2025", "APLICAÇÃO", "CDB - K1 - BANCO X S/A",
         "XP", 950_000, 0.01, 9_500.00],
    ]
    irpf = book(rows, year=2025).set_index("code")["value_cur"].to_dict()

    trades, events = read_movimentacao(tmp_path / "b3_imports" / "movimentacao-test.xlsx")
    positions, _ = derive_positions(trades, cash_events=events)
    parsed = positions.set_index("ticker")["total_cost"].to_dict()

    assert set(irpf) == set(parsed), "the two surfaces disagree about which contracts exist"
    for code, cost in parsed.items():
        assert irpf[code] == pytest.approx(cost, abs=0.01), code
    # And the amortização actually landed, on both.
    assert parsed["CRI - 24L000003F"] == pytest.approx(7870.61)


def test_a_resgate_with_no_visible_aplicacao_is_flagged_not_declared(book):
    """The extract's window opens after the contract was bought, so proceeds
    minus cost is not a gain — it is a missing number. Declaring the full
    proceeds as rendimento would overstate the income by the whole principal."""
    r = only(book([
        ["Debito", "01/11/2025", "VENCIMENTO", "CDB - K2 - BANCO X S/A",
         "XP", 6_200_000, 0.01, 62_000.00],
    ]))
    assert bool(r["incomplete"]) is True
    assert r["rendimento_cur"] == 0.0


def test_an_amortizacao_larger_than_the_visible_cost_cannot_go_negative(book):
    """derive_positions clamps at zero; this must too, or Bens e Direitos prints
    a negative custo de aquisição — and the two surfaces disagree again."""
    r = only(book([
        ["Credito", "15/05/2025", "AMORTIZAÇÃO", "CRI - K3 - OPEA S/A",
         "XP", 8, 51.59, 412.75],
        ["Credito", "15/08/2025", "PAGAMENTO DE JUROS", "CRI - K3 - OPEA S/A",
         "XP", 8, 12.05, 96.40],
    ]))
    assert r["value_cur"] == 0.0
    assert bool(r["incomplete"]) is True


def test_a_full_redemption_reconciles_when_the_report_carries_no_quantity(book):
    """B3 ships extracts with no usable Quantidade. The all-or-nothing fallback
    was dead code — nothing was ever consumed, so a fully redeemed contract
    stayed declared at full cost *and* booked its own principal as income."""
    r = only(book([
        ["Credito", "01/06/2024", "APLICAÇÃO", "CDB - K4 - BANCO X S/A",
         "XP", None, None, 10_000.00],
        ["Debito", "01/03/2025", "RESGATE ANTECIPADO", "CDB - K4 - BANCO X S/A",
         "XP", None, None, 5_200.00],
        ["Debito", "01/11/2025", "VENCIMENTO", "CDB - K4 - BANCO X S/A",
         "XP", None, None, 5_400.00],
    ]))
    assert r["value_prev"] == pytest.approx(10_000.00)
    assert r["value_cur"] == 0.0
    assert r["rendimento_cur"] == pytest.approx(600.00)


@pytest.mark.parametrize("produto, grupo, codigo, label", [
    ("LCI - C - BANCO X S/A", "04", "03", "LCI (renda fixa isenta)"),
    ("LCA - C - BANCO X S/A", "04", "03", "LCA (renda fixa isenta)"),
    ("CRI - C - OPEA SECURITIZADORA S/A", "04", "03", "CRI (renda fixa isenta)"),
    ("CRA - C - OPEA SECURITIZADORA S/A", "04", "03", "CRA (renda fixa isenta)"),
    ("CDB - C - BANCO X S/A", "04", "02", "CDB (renda fixa tributada)"),
    ("RDB - C - BANCO X S/A", "04", "02", "RDB (renda fixa tributada)"),
    ("LC - C - BANCO X S/A", "04", "02", "LC (renda fixa tributada)"),
    ("LF - C - BANCO X S/A", "04", "02", "LF (renda fixa tributada)"),
    ("LFT - C - TESOURO NACIONAL", "04", "02", "LFT (Tesouro Direto)"),
    ("LTN - C - TESOURO NACIONAL", "04", "02", "LTN (Tesouro Direto)"),
    ("NTN - C - TESOURO NACIONAL", "04", "02", "NTN (Tesouro Direto)"),
    ("Tesouro - Selic - TESOURO NACIONAL", "04", "02", "TESOURO (Tesouro Direto)"),
    ("DEB - C - EMPRESA X S/A", "04", "03", "Debênture incentivada"),
    # No ' - ' separator: the whole title is the prefix, which no bucket names,
    # so it falls back to tributado under its own name.
    ("Tesouro Selic 2029", "04", "02", "TESOURO SELIC 2029"),
])
def test_each_prefix_lands_on_its_irpf_codigo_and_label(book, produto, grupo, codigo, label):
    r = only(book([["Credito", "01/06/2025", "APLICAÇÃO", produto,
                    "XP", 1, 1_000.00, 1_000.00]]))
    assert (r["irpf_grupo"], r["irpf_codigo"], r["kind_label"]) == (grupo, codigo, label)
