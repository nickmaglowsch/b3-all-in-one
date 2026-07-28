"""Self-checks for renda fixa ingestion (CDB, LCI, LCA, CRI, CRA, deb, Tesouro).

These contracts are registered at B3, not traded on it: they exist only in the
Movimentação extract, under a contract code instead of a ticker. Everything here
guards a way that identity can be silently corrupted or dropped.
"""
import pandas as pd
import pytest

from scripts.irpf_2026 import asset_class
from scripts.load_b3 import (
    _extract_ticker,
    _rf_code,
    apply_ticker_renames,
    consolidate_fractional,
    derive_positions,
    read_movimentacao,
)

MOV_COLUMNS = ["Entrada/Saída", "Data", "Movimentação", "Produto",
               "Instituição", "Quantidade", "Preço unitário",
               "Valor da Operação"]


def write_mov(path, rows):
    """A minimal Movimentação .xlsx with `rows` in B3's own column order."""
    pd.DataFrame(rows, columns=MOV_COLUMNS).to_excel(path, index=False)
    return path


# --- identity ---------------------------------------------------------------

def test_rf_code_keeps_prefix_and_contract_but_drops_the_issuer():
    # The issuer reformats between reports ("S/A" vs "S.A."), so including it
    # would split one contract into two holdings.
    assert (_rf_code("CDB - CDB24C7HQ2L - BANCO ORIGINAL S/A")
            == "CDB - CDB24C7HQ2L")
    assert (_rf_code("CDB - CDB24C7HQ2L - BANCO ORIGINAL S.A.")
            == "CDB - CDB24C7HQ2L")


def test_tesouro_is_matched_despite_mixed_case_and_no_contract_code():
    # B3 caps the bank paper but writes the Tesouro titles mixed-case; a
    # case-sensitive ^TESOURO would miss every Tesouro Direto row.
    assert _rf_code("Tesouro Selic 2029") == "TESOURO SELIC 2029"


def test_non_renda_fixa_products_are_still_ignored():
    assert _rf_code("PETR4 - PETROLEO BRASILEIRO SA") is None
    assert _rf_code("CFF - 1234 - ALGUMA COISA") is None
    assert _rf_code(None) is None
    # Equities still take the ticker branch, unchanged.
    assert _extract_ticker("PETR4 - PETROLEO BRASILEIRO SA") == "PETR4"


def test_renda_fixa_never_classifies_as_acao():
    # A CDB resgate booked as a stock sale would inflate the month's AÇÃO gross
    # and can blow the R$20k isenção for genuine equity sales.
    assert asset_class("CDB - CDB24C7HQ2L") == "RF"
    assert asset_class("TESOURO SELIC 2029") == "RF"
    assert asset_class("PETR4") == "AÇÃO"


# --- the F strip ------------------------------------------------------------

# A contract code ending in F is truncated by a bare `F$` strip — and taxcore
# strips at three different pipeline stages, so the layers would disagree about
# one book. Guarded by the round-lot shape instead.
F_CODE = "LCI - LCI24H9RKF"


def test_ticker_renames_leave_an_f_ending_contract_code_alone():
    out = apply_ticker_renames(pd.DataFrame({"ticker": [F_CODE, "TRPL4F"]}))
    assert list(out["ticker"]) == [F_CODE, "ISAE4F"]


def test_consolidation_leaves_an_f_ending_contract_code_alone():
    positions = pd.DataFrame({
        "ticker": [F_CODE, "CMIG4F", "CMIG4"],
        "qty": [1000.0, 3.0, 100.0],
        "avg_cost": [1.0, 10.0, 10.0],
        "total_cost": [1000.0, 30.0, 1000.0],
        "market": ["Renda Fixa", "Mercado Fracionário", "Mercado à Vista"],
    })
    out = consolidate_fractional(positions)
    assert set(out["ticker"]) == {F_CODE, "CMIG4"}
    assert float(out.loc[out["ticker"] == "CMIG4", "qty"].iloc[0]) == 103.0


def test_derive_positions_leaves_an_f_ending_contract_code_alone():
    trades = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-10"]),
        "side": ["Compra"], "market": ["Renda Fixa"], "maturity": ["-"],
        "broker": ["XP"], "ticker": [F_CODE], "qty": [1000.0],
        "price": [1.0], "amount": [1000.0], "signed_qty": [1000.0],
    })
    positions, _ = derive_positions(trades)
    assert list(positions["ticker"]) == [F_CODE]


# --- quantity conventions ---------------------------------------------------

# B3 denominates the same R$25,000 CDB two ways depending on the report, and a
# third form carries only the amount. All three describe one holding, so the
# parser must land on identical economics for each — that is what makes the
# convention not worth guessing at.
CONVENTIONS = [
    pytest.param(2_500_000, 0.01, id="units-with-pu"),
    pytest.param(1, 25_000.00, id="single-unit"),
    pytest.param(None, None, id="amount-only"),
]


@pytest.mark.parametrize("qty, pu", CONVENTIONS)
def test_quantity_convention_does_not_change_the_economics(tmp_path, qty, pu):
    exit_pu = None if pu is None else pu * 1.21
    path = write_mov(tmp_path / "movimentacao-conv.xlsx", [
        ["Credito", "14/03/2024", "APLICAÇÃO", "CDB - X1 - BANCO ORIGINAL S/A",
         "XP", qty, pu, 25_000.00],
        ["Debito", "16/03/2026", "VENCIMENTO", "CDB - X1 - BANCO ORIGINAL S/A",
         "XP", qty, exit_pu, 30_250.00],
    ])
    trades, _events = read_movimentacao(path)

    assert list(trades["ticker"]) == ["CDB - X1", "CDB - X1"]
    assert list(trades["market"]) == ["Renda Fixa", "Renda Fixa"]
    assert list(trades["side"]) == ["Compra", "Venda"]
    # qty x price reconstructs the Valor B3 reported, whatever the denomination.
    for _, r in trades.iterrows():
        assert r["qty"] * r["price"] == pytest.approx(r["amount"], abs=1e-6)

    positions, _incomplete = derive_positions(trades)
    # Bought and redeemed in full: nothing is still held.
    assert positions.empty or float(positions["qty"].sum()) == pytest.approx(0.0)


def test_open_contract_keeps_its_cost_at_the_reported_amount(tmp_path):
    path = write_mov(tmp_path / "movimentacao-open.xlsx", [
        ["Credito", "09/06/2025", "APLICAÇÃO", "CDB - X2 - BANCO C6 S.A.",
         "XP", 1_800_000, 0.01, 18_000.00],
    ])
    trades, _events = read_movimentacao(path)
    positions, _ = derive_positions(trades)
    row = positions[positions["ticker"] == "CDB - X2"].iloc[0]
    assert float(row["total_cost"]) == pytest.approx(18_000.00)
    assert row["market"] == "Renda Fixa"


# --- custody freezes --------------------------------------------------------

def test_valueless_custody_withdrawal_is_not_a_sale(tmp_path):
    """An issuer under intervention (FGC pending) still leaves the paper held.

    Booking it as a redemption would report money back that never arrived, and
    in the index it would land as a fabricated one-day total loss.
    """
    path = write_mov(tmp_path / "movimentacao-frozen.xlsx", [
        ["Credito", "20/01/2025", "APLICAÇÃO", "CDB - X3 - BANCO SINCERO S/A",
         "XP", 950_000, 0.01, 9_500.00],
        ["Debito", "15/01/2026", "RETIRADA DE CUSTÓDIA",
         "CDB - X3 - BANCO SINCERO S/A", "XP", 950_000, 0, 0],
    ])
    trades, events = read_movimentacao(path)

    assert list(trades["side"]) == ["Compra"]          # no sell was invented
    assert list(events["event_category"]) == ["frozen"]  # but it is on record

    positions, _ = derive_positions(trades)
    row = positions[positions["ticker"] == "CDB - X3"].iloc[0]
    assert float(row["total_cost"]) == pytest.approx(9_500.00)


def test_custody_withdrawal_that_paid_is_a_sale(tmp_path):
    path = write_mov(tmp_path / "movimentacao-paid.xlsx", [
        ["Credito", "20/01/2025", "APLICAÇÃO", "CDB - X4 - BANCO SINCERO S/A",
         "XP", 950_000, 0.01, 9_500.00],
        ["Debito", "15/01/2026", "RETIRADA DE CUSTÓDIA",
         "CDB - X4 - BANCO SINCERO S/A", "XP", 950_000, 0.011, 10_450.00],
    ])
    trades, _events = read_movimentacao(path)
    assert list(trades["side"]) == ["Compra", "Venda"]
