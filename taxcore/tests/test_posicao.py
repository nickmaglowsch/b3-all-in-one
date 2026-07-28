"""Self-checks for the relatório consolidado mensal ("posição") reader.

This is the third extract B3 publishes and the only one that prices a contract
while it is still held. Neither Negociação nor Movimentação does: they report
fills, so a CDB nobody has redeemed has exactly one price in the whole book and
one price implies no rate. The posição report carries `Preço Atualizado`, a
per-unit number the *issuer* computed — a second mark, from which the rate falls
out exactly. That is why this reader exists, and everything here guards the two
ways that mark can be silently wrong: attached to the wrong contract, or dated
to the wrong day.
"""
import datetime

import pandas as pd
import pytest

from scripts import load_b3
from scripts.load_b3 import (
    POSICAO_RF_SHEET,
    _posicao_date,
    detect_file_type,
    read_posicao,
)
from test_renda_fixa import write_mov

POS_COLUMNS = ["Produto", "Instituição", "Emissor", "Código", "Indexador",
               "Tipo de regime", "Data de Emissão", "Vencimento", "Quantidade",
               "Preço Atualizado MTM", "Valor Atualizado MTM",
               "Preço Atualizado CURVA", "Valor Atualizado CURVA"]


def _row(produto, codigo, mtm="-", curva="-", indexador="DI"):
    return [produto, "ITAU CV S/A", "BANCO X", codigo, indexador, "DEPOSITADO",
            "26/03/2025", "27/03/2028", 10.0, mtm, "-", curva, "-"]


def write_posicao(tmp_path, rows, name="relatorio-consolidado-mensal-2026-junho.xlsx"):
    """A minimal posição report: the renda fixa sheet under B3's own name."""
    path = tmp_path / name
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        pd.DataFrame([["BBAS3 - BCO BRASIL"]], columns=["Produto"]).to_excel(
            xl, sheet_name="Posição - Ações", index=False)
        pd.DataFrame(rows, columns=POS_COLUMNS).to_excel(
            xl, sheet_name=POSICAO_RF_SHEET, index=False)
    return path


# --- the date ---------------------------------------------------------------

def test_the_reference_month_becomes_a_month_end_mark_date():
    # Nothing inside the workbook says when the position was taken — the renda
    # fixa sheet has no reference-date column and the Negociações sheet's
    # "Período (Final)" is a literal "-". The filename is the only source, and
    # the report is the position at month close.
    assert (_posicao_date("relatorio-consolidado-mensal-2026-junho.xlsx")
            == datetime.date(2026, 6, 30))
    assert (_posicao_date("relatorio-consolidado-mensal-2024-fevereiro.xlsx")
            == datetime.date(2024, 2, 29))          # leap year, not a fixed 28
    # Accents and a numeric month, in case B3's filename ever changes shape.
    assert (_posicao_date("relatorio-consolidado-mensal-2026-março.xlsx")
            == datetime.date(2026, 3, 31))
    assert (_posicao_date("relatorio-consolidado-mensal-2026-03.xlsx")
            == datetime.date(2026, 3, 31))


def test_a_second_download_of_the_same_month_still_reads_as_that_month():
    # A browser that already has the file saves the next one as "... (1).xlsx",
    # which is how a real upload most often arrives. Refusing it would fail the
    # tenant's whole rebuild, not just this file — and the (date, ticker) dedupe
    # in main() is what makes reading both copies harmless.
    assert (_posicao_date("relatorio-consolidado-mensal-2026-junho (1).xlsx")
            == datetime.date(2026, 6, 30))
    assert (_posicao_date("relatorio-consolidado-mensal-2026-03 (2).xlsx")
            == datetime.date(2026, 3, 31))


def test_a_report_renamed_past_recognition_fails_loudly():
    # Guessing a date here would date every mark wrong, which bends the implied
    # rate of every contract in the file. Refusing names the fix; a silent skip
    # would leave the user staring at an unchanged chart with no reason given.
    with pytest.raises(SystemExit, match="posição"):
        _posicao_date("junho.xlsx")
    with pytest.raises(SystemExit):
        _posicao_date("relatorio-consolidado-mensal-2026-junnho.xlsx")


# --- identity ---------------------------------------------------------------

def test_the_mark_joins_on_the_contract_code_movimentacao_writes(tmp_path):
    # The posição report splits what Movimentação keeps in one cell: "CDB -
    # BANCO AGIBANK S/A" in Produto, "CDB325AQWAM" in Código. _rf_code derives
    # "CDB - CDB325AQWAM" from the Movimentação side, and nothing maps between
    # the two — this string is the whole join.
    path = write_posicao(tmp_path, [
        _row("CDB - BANCO AGIBANK S/A", "CDB325AQWAM", curva=1206.40847)])
    out = read_posicao(path)

    assert list(out["ticker"]) == ["CDB - CDB325AQWAM"]
    assert list(out["date"]) == [datetime.date(2026, 6, 30)]


def test_products_that_are_not_renda_fixa_contracts_are_dropped(tmp_path):
    # The sheet also carries FIP quotas under a "CFF -" prefix, and closes with
    # a totals row whose cells are blank. Neither is a contract; letting the
    # totals row through would invent a holding priced at the book's sum.
    path = write_posicao(tmp_path, [
        _row("CDB - BANCO BMG S/A", "CDB4267Z827", curva=1026.769769),
        _row("CFF - XP PRIVATE EQUITY II FIP", "4827123XPA", curva=1074.28),
        _row(None, None, curva=505279.87),
    ])
    assert list(read_posicao(path)["ticker"]) == ["CDB - CDB4267Z827"]


# --- the price --------------------------------------------------------------

def test_mtm_is_the_mark_when_b3_publishes_one(tmp_path):
    # Two price columns, and they are not the same claim. MTM is what the paper
    # would fetch today; CURVA is the contracted accrual, which ignores that
    # rates have moved. Prefer the market number wherever B3 has one.
    path = write_posicao(tmp_path, [
        _row("CRI - EMISSOR", "CRI9243BUWL", mtm=980.5, curva=1010.0)])
    assert read_posicao(path)["price"].iloc[0] == pytest.approx(980.5)


def test_curva_carries_the_row_when_mtm_is_a_dash(tmp_path):
    # Every CDB row in a real report has MTM "-": bank paper has no secondary
    # market to mark against. CURVA is then the only number B3 publishes, and
    # for paper held to maturity it is also the honest one.
    path = write_posicao(tmp_path, [
        _row("CDB - BANCO C6 S.A.", "CDB6259RTN9", mtm="-", curva=1149.314729)])
    assert read_posicao(path)["price"].iloc[0] == pytest.approx(1149.314729)


def test_a_row_with_no_usable_price_is_dropped_not_marked_at_zero(tmp_path):
    # A zero would be read downstream as a per-unit price of zero and draw the
    # contract to a total loss on the report date.
    path = write_posicao(tmp_path, [
        _row("CDB - BANCO PAN S/A", "CDB323NM783", mtm="-", curva="-"),
        _row("CDB - BANCO PINE S/A", "CDBC248TQ01", curva=0.0),
    ])
    assert read_posicao(path).empty


# --- classification ---------------------------------------------------------

def test_the_report_is_classified_by_its_own_sheet_not_the_first_one(tmp_path):
    # Sheet 0 of a posição report is "Posição - Ações", whose columns match
    # neither of the other two extracts — sniffing it would raise instead of
    # classifying. The renda fixa sheet's name is the signature.
    path = write_posicao(tmp_path, [
        _row("CDB - BANCO BMG S/A", "CDB4267Z827", curva=1026.77)])
    assert detect_file_type(path) == "posicao"


# --- end to end -------------------------------------------------------------

def test_a_posicao_alongside_a_movimentacao_marks_the_contract_it_bought(
        tmp_path, monkeypatch):
    """The whole point, through `main`: an aplicação in one file and the issuer's
    Preço Atualizado in the other land on the same ticker, which is the only way
    the two ever meet — nothing maps between "CDB - CDB325AQWAM - BANCO AGIBANK
    S/A" in Movimentação and Produto+Código split across two cells here."""
    write_mov(tmp_path / "movimentacao-2025.xlsx", [
        ["Credito", "26/03/2025", "APLICAÇÃO",
         "CDB - CDB325AQWAM - BANCO AGIBANK S/A", "ITAU CV S/A", 10, 1000.0, 10000.0],
    ])
    write_posicao(tmp_path, [
        _row("CDB - BANCO AGIBANK S/A", "CDB325AQWAM", curva=1206.40847)])

    data = tmp_path / "data"
    monkeypatch.setattr(load_b3, "ROOT", tmp_path)
    monkeypatch.setattr(load_b3, "IMPORTS_DIR", tmp_path)
    monkeypatch.setattr(load_b3, "DATA_DIR", data)
    for attr, name in [("TRADES_CSV", "trades.csv"),
                       ("CASH_EVENTS_CSV", "cash_events.csv"),
                       ("POSITIONS_CSV", "positions.csv"),
                       ("RF_MARKS_CSV", "rf_marks.csv"),
                       ("OVERRIDES_CSV", "cost_basis_overrides.csv")]:
        monkeypatch.setattr(load_b3, attr, data / name)

    assert load_b3.main(["load_b3", "--all-markets"]) == 0

    marks = pd.read_csv(data / "rf_marks.csv")
    assert marks.to_dict("records") == [
        {"date": "2026-06-30", "ticker": "CDB - CDB325AQWAM",
         "price": pytest.approx(1206.40847)}]
    # The mark is a price observation, not an event: nothing was bought, sold or
    # paid, so it must not appear anywhere a count or an apuração would see it.
    # Booking it as a zero-quantity trade would have saved a CSV and then shown
    # up in the trade list, the apuração and the position's average cost.
    trades = pd.read_csv(data / "trades.csv")
    assert list(trades["ticker"]).count("CDB - CDB325AQWAM") == 1
    assert "2026-06-30" not in set(trades["date"].astype(str))
    events = pd.read_csv(data / "cash_events.csv")
    assert "CDB - CDB325AQWAM" not in set(events["ticker"].dropna())
