"""The import side of the taxcore audit: each test is one way the B3 extracts
used to be read into a wrong number.

  - two identical fills in one report were deduped into one (H5)
  - a bonificação entered at zero cost (H6)
  - a grupamento was skipped, and subscription events vanished unannounced (H7)
  - a Movimentação-only book dated every trade by settlement, D+2 (M5)
  - BDR dividends were filed as isentos, código 09 (M6)
  - an auctioned fraction was no sale at all (L2)
  - an oversell left positions.csv with a negative holding (M1, positions half)
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from scripts import b3_rules, irpf_2026, irpf_proventos, load_b3, tlh_plan
from scripts.b3_options import _resolve_exercise_underlying
from scripts.load_b3 import derive_positions, parse_movimentacao

MOV_COLS = ["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição",
            "Quantidade", "Preço unitário", "Valor da Operação"]
NEG_COLS = ["Data do Negócio", "Tipo de Movimentação", "Mercado", "Prazo/Vencimento",
            "Instituição", "Código de Negociação", "Quantidade", "Preço", "Valor"]
ITAU, XP = "ITAU CV S/A", "XP INVESTIMENTOS CCTVM S/A"
PETR = "PETR4 - PETROLEO BRASILEIRO S/A PETROBRAS"


def neg(day, side, ticker, qty, price):
    return [day, side, "Mercado à Vista", "-", ITAU, ticker, qty, price, qty * price]


def mov(direction, day, label, produto, qty, price="-", valor="-", broker=ITAU):
    return [direction, day, label, produto, broker, qty, price, valor]


def write(path, cols, rows):
    pd.DataFrame(rows, columns=cols).to_excel(path, index=False)
    return path


@pytest.fixture
def run_main(tmp_path, monkeypatch, capsys):
    """Run load_b3.main over the extracts written into an isolated ROOT."""
    imports, data = tmp_path / "b3_imports", tmp_path / "data"
    imports.mkdir()
    monkeypatch.setattr(load_b3, "ROOT", tmp_path)
    monkeypatch.setattr(load_b3, "IMPORTS_DIR", imports)
    monkeypatch.setattr(load_b3, "DATA_DIR", data)
    for attr, name in [("TRADES_CSV", "trades.csv"), ("CASH_EVENTS_CSV", "cash_events.csv"),
                       ("POSITIONS_CSV", "positions.csv"), ("RF_MARKS_CSV", "rf_marks.csv"),
                       ("OVERRIDES_CSV", "cost_basis_overrides.csv")]:
        monkeypatch.setattr(load_b3, attr, data / name)

    def run(files: dict[str, tuple[list, list]], overrides: str | None = None):
        for name, (cols, rows) in files.items():
            write(imports / name, cols, rows)
        if overrides is not None:
            data.mkdir(exist_ok=True)
            (data / "cost_basis_overrides.csv").write_text(overrides)
        assert load_b3.main(["load_b3"]) == 0
        out = capsys.readouterr()
        return (json.loads(out.out), out.err, pd.read_csv(data / "trades.csv"),
                pd.read_csv(data / "cash_events.csv"), pd.read_csv(data / "positions.csv"))
    return run


def trades(*rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "side": s, "market": "Mercado à Vista",
                          "ticker": t, "qty": q, "price": p,
                          "signed_qty": q if s == "Compra" else -q} for d, s, t, q, p in rows])


def events(*rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "ticker": t, "event_type": et,
                          "event_category": cat, "is_amortization": False, "qty": q,
                          "price": p, "amount": a, "broker": ITAU, "__source_file": "m.xlsx"}
                         for d, t, et, cat, q, p, a in rows])


@pytest.fixture
def csvs(tmp_path, monkeypatch):
    """Point irpf_2026 at a trades/cash_events pair and build its timeline."""
    def build(t: pd.DataFrame, e: pd.DataFrame) -> pd.DataFrame:
        t.to_csv(tmp_path / "trades.csv", index=False)
        e.to_csv(tmp_path / "cash_events.csv", index=False)
        monkeypatch.setattr(irpf_2026, "TRADES", tmp_path / "trades.csv")
        monkeypatch.setattr(irpf_2026, "EVENTS", tmp_path / "cash_events.csv")
        return irpf_2026.build_timeline()
    return build


# --- H5: dedup collapses re-exports, never fills -----------------------------

KEY = ["date", "side", "ticker", "qty", "price"]


def _fills(source, n):
    return pd.DataFrame([{"date": "2025-03-10", "side": "Compra", "ticker": "PETR4",
                          "qty": 100.0, "price": 30.0, "__source_file": source}] * n)


def test_two_identical_fills_in_one_report_are_two_trades():
    assert len(b3_rules.drop_reexported(_fills("a.xlsx", 2), KEY)) == 2


def test_a_reexport_collapses_pairwise_against_the_report_it_overlaps():
    both = pd.concat([_fills("a.xlsx", 2), _fills("b.xlsx", 2)], ignore_index=True)
    assert len(b3_rules.drop_reexported(both, KEY)) == 2
    # A narrower re-export holding one of the two fills adds nothing either.
    narrow = pd.concat([_fills("a.xlsx", 2), _fills("b.xlsx", 1)], ignore_index=True)
    assert len(b3_rules.drop_reexported(narrow, KEY)) == 2


def test_identical_fills_survive_the_whole_import_and_reexports_still_collapse(run_main):
    rows = [neg("10/03/2025", "Compra", "PETR4", 100, 30.0),
            neg("10/03/2025", "Compra", "PETR4", 100, 30.0),
            neg("12/03/2025", "Venda", "PETR4", 200, 35.0)]
    summary, _err, t, _e, pos = run_main({
        "negociacao-a.xlsx": (NEG_COLS, rows),
        "negociacao-a-copia.xlsx": (NEG_COLS, rows)})
    assert (t["side"] == "Compra").sum() == 2
    assert summary["duplicates_removed"] == 3
    assert summary["incomplete_history"] == []
    assert pos.empty


def test_one_dividend_at_each_of_two_brokers_is_two_dividends(run_main):
    # Movimentação lists every broker in one file, and the dedup key leaves
    # broker out — so the second payment used to vanish.
    rows = [mov("Credito", "20/03/2025", "Dividendo", PETR, 100, "-", 50.0),
            mov("Credito", "20/03/2025", "Dividendo", PETR, 100, "-", 50.0, broker=XP)]
    _s, _err, _t, ev, _p = run_main({
        "negociacao.xlsx": (NEG_COLS, [neg("03/03/2025", "Compra", "PETR4", 200, 30.0)]),
        "movimentacao.xlsx": (MOV_COLS, rows),
        "movimentacao-copia.xlsx": (MOV_COLS, rows)})
    assert ev.loc[ev["event_type"] == "Dividendo", "amount"].sum() == pytest.approx(100.0)


# --- H6: bonificação carries its custo atribuído ------------------------------

BONUS = ("2025-06-10", "GOAU4", "Bonificação em Ativos", "qty_event", 100.0, 8.9, 0.0)


def test_bonificacao_keeps_bs_cost_and_a_split_never_carries_one(tmp_path):
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Credito", "10/06/2025", "Bonificação em Ativos",
            "GOAU4 - METALURGICA GERDAU S.A.", 75, 8.90),
        mov("Credito", "22/04/2025", "Desdobro", "WEGE3 - WEG S.A.", 100, 12.0)])
    _t, ev, review = parse_movimentacao(path)
    bonus = ev[ev["event_type"] == "Bonificação em Ativos"].iloc[0]
    assert (bonus["qty"], bonus["price"], bonus["amount"]) == (75.0, 8.9, 0.0)
    # A price on a split is not a cost: total cost does not move on a desdobro.
    assert pd.isna(ev.loc[ev["event_type"] == "Desdobro", "price"].iloc[0])
    assert review == []


def test_bonificacao_adds_its_cost_to_the_position():
    pos, _ = derive_positions(trades(("2025-01-02", "Compra", "GOAU4", 100, 10.0)),
                              events(BONUS))
    assert pos.iloc[0]["qty"] == 200
    assert pos.iloc[0]["total_cost"] == pytest.approx(1890.0)


def test_bonificacao_cost_reaches_the_apuracao_and_bens_e_direitos(csvs):
    """Lei 9.249/95 art. 10 p.ú.: 100 bought for R$ 1.000, 100 bonus at R$ 8,90,
    all 200 sold for R$ 2.000 — the gain is R$ 110, not R$ 1.000."""
    tl = csvs(trades(("2025-01-02", "Compra", "GOAU4", 100, 10.0),
                     ("2025-08-01", "Venda", "GOAU4", 200, 10.0)), events(BONUS))
    assert irpf_2026.realized_per_month(tl, 2025)["pnl"].sum() == pytest.approx(110.0)
    snap = irpf_2026.snapshot_positions(tl, pd.Timestamp("2025-06-30"))
    assert snap.iloc[0]["total_cost"] == pytest.approx(1890.0)


def test_bonificacao_cost_reaches_the_tlh_walk(csvs):
    today = pd.Timestamp.today().normalize()
    tl = csvs(trades((today - pd.Timedelta(days=400), "Compra", "GOAU4", 100, 10.0),
                     (today, "Venda", "GOAU4", 200, 10.0)),
              events((today - pd.Timedelta(days=200), *BONUS[1:])))
    _carry, mtd = tlh_plan.walk_history(tl)
    assert mtd["AÇÃO"]["pnl"] == pytest.approx(110.0)


def test_a_bonificacao_without_a_cost_is_named_not_guessed(tmp_path):
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Credito", "10/06/2025", "Bonificação em Ativos",
            "GOAU4 - METALURGICA GERDAU S.A.", 75)])
    (review,) = parse_movimentacao(path)[2]
    assert review["ticker"] == "GOAU4" and "custo zero" in review["reason"]


def test_codigo_18_lists_the_bonificacao_the_cost_came_from():
    yr = events(BONUS, ("2025-04-22", "WEGE3", "Desdobro", "qty_event", 100.0, None, 0.0))
    text = "\n".join(irpf_proventos.lines(pd.DataFrame(columns=["ticker", "amount"]), yr, 2025))
    assert "Código 18" in text and "| 2025-06-10 | GOAU4 | 100 | 8.90 | 890.00 |" in text
    assert "WEGE3" not in text


# --- H7: grupamento and subscriptions ----------------------------------------

def test_grupamento_cuts_the_quantity_and_keeps_the_cost(tmp_path, csvs):
    """1.000 @ R$ 10, then 10:1: 100 shares, still R$ 10.000. Skipped, the 100
    sold at R$ 100 booked R$ 9.000 of gain and 900 phantom shares."""
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Debito", "05/05/2025", "Grupamento", PETR, 900)])
    _t, ev, review = parse_movimentacao(path)
    assert review == []
    buy, sell = (("2025-01-02", "Compra", "PETR4", 1000, 10.0),
                 ("2025-06-02", "Venda", "PETR4", 100, 100.0))
    pos, _ = derive_positions(trades(buy), ev)
    assert (pos.iloc[0]["qty"], pos.iloc[0]["total_cost"]) == (100, 10000.0)
    tl = csvs(trades(buy, sell), ev)
    assert irpf_2026.realized_per_month(tl, 2025)["pnl"].sum() == pytest.approx(0.0)


def test_subscription_and_unknown_labels_are_named_never_silently_skipped(tmp_path):
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Credito", "28/02/2025", "Direito de Subscrição", "MXRF11 - MAXI RENDA", 128),
        mov("Debito", "15/03/2025", "Direitos de Subscrição - Exercido",
            "MXRF11 - MAXI RENDA", 128, 9.5, 1216.0),
        mov("Credito", "15/03/2025", "Evento Novo da B3", PETR, 10),
        mov("Credito", "15/03/2025", "Empréstimo", PETR, 10)])
    t, ev, review = parse_movimentacao(path)
    assert t.empty and ev.empty  # nothing booked on a guess
    assert [(r["label"], r["ticker"]) for r in review] == [
        ("Direitos de Subscrição - Exercido", "MXRF11"), ("Evento Novo da B3", "PETR4")]
    assert "preço de subscrição" in review[0]["reason"]


def test_review_rows_reach_the_summary_and_stderr_unless_an_override_answers(run_main):
    rows = [mov("Debito", "15/03/2025", "Direitos de Subscrição - Exercido",
                "MXRF11 - MAXI RENDA", 128, 9.5, 1216.0),
            mov("Credito", "30/09/2024", "Atualização", "XPBR31 - XP INC BDR", 10)]
    overrides = ("date,ticker,kind,qty,price,amount,note\n"
                 "2024-09-30,XPBR31,buy,10,84.17,,cisao\n")
    summary, err, *_ = run_main({
        "negociacao.xlsx": (NEG_COLS, [neg("03/03/2025", "Compra", "MXRF11", 10, 10.0)]),
        "movimentacao.xlsx": (MOV_COLS, rows)}, overrides=overrides)
    assert [r["ticker"] for r in summary["movimentacao_review"]] == ["MXRF11"]
    assert "Direitos de Subscrição - Exercido" in err


# --- M5: Movimentação-only books date trades by trade, not settlement --------

def test_easter_and_the_b3_calendar():
    assert [b3_rules._easter(y) for y in (2024, 2025, 2026)] == [
        dt.date(2024, 3, 31), dt.date(2025, 4, 20), dt.date(2026, 4, 5)]
    hol = set(b3_rules.b3_holidays([2021, 2023, 2025]))
    assert dt.date(2025, 3, 3) in hol and dt.date(2025, 6, 19) in hol  # Carnaval, Corpus
    assert dt.date(2021, 1, 25) in hol and dt.date(2023, 1, 25) not in hol
    assert dt.date(2025, 11, 20) in hol and dt.date(2023, 11, 20) not in hol


@pytest.mark.parametrize("settled, traded", [
    ("2025-02-03", "2025-01-30"),  # a January sale, settled in February
    ("2025-03-06", "2025-02-28"),  # across Carnaval
    ("2025-01-04", "2025-01-02"),  # a Saturday (only synthetic data) rolls forward
])
def test_settlement_walks_back_two_b3_business_days(settled, traded):
    got = b3_rules.settlement_to_trade_date(pd.Series([pd.Timestamp(settled)]))
    assert got.iloc[0] == pd.Timestamp(traded)


def test_liquidacao_rows_are_dated_by_trade_and_renda_fixa_is_left_alone(tmp_path):
    path = write(tmp_path / "m.xlsx", MOV_COLS, [
        mov("Debito", "03/02/2025", "Transferência - Liquidação", PETR, 100, 30.0, 3000.0),
        mov("Credito", "03/02/2025", "APLICAÇÃO", "CDB - CDB1 - BANCO X S/A",
            1000, 1.0, 1000.0)])
    t, _ev, _review = parse_movimentacao(path)
    dates = dict(zip(t["ticker"], t["date"]))
    assert dates["PETR4"] == pd.Timestamp("2025-01-30")
    assert dates["CDB - CDB1"] == pd.Timestamp("2025-02-03")


def test_a_movimentacao_only_book_says_its_dates_are_inferred(run_main):
    summary, err, t, *_ = run_main({"movimentacao.xlsx": (MOV_COLS, [
        mov("Credito", "03/02/2025", "Transferência - Liquidação", PETR, 100, 30.0, 3000.0)])})
    assert summary["trade_dates_from_settlement"] is True
    assert "day trades are invisible" in err
    assert t["date"].tolist() == ["2025-01-30"]


def test_exercise_matches_a_settlement_dated_back_to_or_before_it():
    ex = pd.DataFrame([{"date": pd.Timestamp("2025-03-18"), "qty": 300.0, "price": 12.0}])
    settle = pd.DataFrame([
        {"date": pd.Timestamp("2025-03-25"), "ticker": "XXXX3", "qty": 300.0, "price": 12.0},
        {"date": pd.Timestamp("2025-03-17"), "ticker": "CMIG4", "qty": 300.0, "price": 12.0}])
    assert _resolve_exercise_underlying(ex, settle) == {0: "CMIG4"}


# --- M6: BDR dividends are taxable, not código 09 ---------------------------

def test_bdr_dividends_leave_codigo_09_for_carne_leao():
    div = events(("2025-05-10", "AAPL34", "Dividendo", "income", 10.0, None, 12.5),
                 ("2025-05-10", "PETR4", "Dividendo", "income", 10.0, None, 40.0),
                 ("2025-05-10", "PETR4", "Dividendo", "reembolso", 0.0, None, 1.0))
    isento, bdr = irpf_proventos.split_bdr_dividends(div, irpf_2026.asset_class)
    assert isento["ticker"].tolist() == ["PETR4"] and bdr["ticker"].tolist() == ["AAPL34"]
    text = "\n".join(irpf_proventos.lines(bdr, div.iloc[0:0], 2025))
    assert "carnê-leão" in text and "| AAPL34 | 12.50 |" in text


# --- L2: an auctioned fraction is a sale -------------------------------------

FRAC = ("2025-06-11", "GOAU4", "Fração em Ativos", "qty_event", -0.4, None, 0.0)
AUCTION = ("2025-06-12", "GOAU4", "Leilão de Fração", "other", 0.4, 9.87, 3.95)


def test_fraction_and_its_auction_become_one_sale():
    sales, ev = b3_rules.fraction_auction_sales(events(BONUS, FRAC, AUCTION))
    (sale,) = sales.to_dict("records")
    assert (sale["side"], sale["qty"], sale["amount"]) == ("Venda", 0.4, 3.95)
    assert sale["date"] == pd.Timestamp("2025-06-12")
    # The debit is now the sale's; walking both would drop the quantity twice.
    assert ev.loc[ev["event_type"] == "Fração em Ativos", "event_category"].item() == "other"


@pytest.mark.parametrize("rows", [
    (FRAC,),                                                        # auction not loaded yet
    (AUCTION,),                                                     # debit not loaded
    (("2025-01-02", *FRAC[1:]), AUCTION),                           # too far apart
    (("2025-06-11", "VALE3", *FRAC[2:]), AUCTION),                  # another ticker
])
def test_an_unpaired_fraction_or_auction_is_left_as_it_was(rows):
    sales, ev = b3_rules.fraction_auction_sales(events(*rows))
    assert sales.empty and ev["event_category"].tolist() == [r[3] for r in rows]


def test_the_fraction_sale_leaves_the_rest_at_the_same_preco_medio():
    # Before, the debit dropped 0.4 shares and left their cost on the other 10.
    sales, ev = b3_rules.fraction_auction_sales(events(FRAC, AUCTION))
    pos, _ = derive_positions(pd.concat([trades(("2025-01-02", "Compra", "GOAU4", 10.4, 9.0)),
                                         sales], ignore_index=True), ev)
    assert pos.iloc[0]["qty"] == pytest.approx(10.0)
    assert pos.iloc[0]["avg_cost"] == pytest.approx(9.0)


# --- M1 (positions half): an oversell is clamped, not carried negative -------

def test_an_oversell_does_not_poison_the_next_buy():
    pos, incomplete = derive_positions(trades(("2025-01-02", "Compra", "PETR4", 100, 30.0),
                                              ("2025-01-03", "Venda", "PETR4", 200, 35.0),
                                              ("2025-02-03", "Compra", "PETR4", 300, 40.0)))
    assert (pos.iloc[0]["qty"], pos.iloc[0]["avg_cost"]) == (300, 40.0)
    assert incomplete == ["PETR4"]
