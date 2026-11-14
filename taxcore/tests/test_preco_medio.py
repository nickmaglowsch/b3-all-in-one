"""One preço médio walk for positions.csv and for the IRPF report.

`load_b3.derive_positions` (positions.csv → portfolio, TLH) and
`realizacao` (apuração, Bens e Direitos) used to walk the same events with two
copies of the transition rules, and the copies had drifted: a same-day round
trip, a split credited to a flat position and a grupamento that took more than
was held each came out differently on the two surfaces. Each test states one of
those events on both surfaces and asserts that they agree — and on what.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts import irpf_2026
from scripts.ativos_b3 import asset_class
from scripts.load_b3 import derive_positions
from scripts.preco_medio import Posicao
from scripts.realizacao import apurar, realizar, snapshot_positions
from scripts.relatorio_rv import _avisos, avisos

DATA = Path(__file__).resolve().parents[1] / "data"
FIM = pd.Timestamp("2025-12-31")


def book(*rows):
    """(date, kind, ticker, qty, price) rows, kind ∈ buy/sell/split → the same
    history as load_b3's (trades, cash_events) and as a build_timeline frame."""
    trades, events, tl = [], [], []
    for d, kind, ticker, qty, price in rows:
        d = pd.Timestamp(d)
        if kind in ("buy", "sell"):
            sq = qty if kind == "buy" else -qty
            trades.append({"date": d, "side": "Compra" if kind == "buy" else "Venda",
                           "market": "Mercado à Vista", "ticker": ticker, "qty": qty,
                           "price": price, "signed_qty": sq, "broker": "XP"})
            tl.append({"ticker": ticker, "date": d, "kind": kind, "qty": float(qty),
                       "amount": qty * price, "broker": "XP"})
        else:
            events.append({"date": d, "ticker": ticker, "event_type": "Desdobro",
                           "event_category": "qty_event", "is_amortization": False,
                           "qty": float(qty), "price": None, "amount": 0.0,
                           "broker": "XP"})
            tl.append({"ticker": ticker, "date": d, "kind": "qty_event",
                       "qty": float(qty), "amount": 0.0, "broker": "XP"})
    return pd.DataFrame(trades), pd.DataFrame(events), pd.DataFrame(tl)


def both(*rows):
    """(positions.csv row or None, incomplete, Bens e Direitos row or None)."""
    trades, events, tl = book(*rows)
    pos, incomplete = derive_positions(trades, events if not events.empty else None)
    snap = snapshot_positions(tl, FIM)
    return (pos.iloc[0] if not pos.empty else None, incomplete,
            snap.iloc[0] if not snap.empty else None, tl)


# --- same-day buy and sell at one broker -------------------------------------

def test_a_day_trade_leaves_the_swing_preco_medio_alone_on_both_surfaces():
    """100 held at 20; one day, at one broker, buy 100 @ 40 and sell 100 @ 41.
    The day trade is its own operation (Lei 9.959/2000, art. 8º): the swing
    position is still 100 at 20. positions.csv averaged the buy in and said
    R$ 3.000 — and TLH priced its harvest off that."""
    pos, incomplete, snap, _tl = both(("2025-01-02", "buy", "PETR4", 100, 20.0),
                                      ("2025-07-01", "buy", "PETR4", 100, 40.0),
                                      ("2025-07-01", "sell", "PETR4", 100, 41.0))
    assert (pos["qty"], pos["total_cost"]) == (100, pytest.approx(2_000.0))
    assert (snap["qty"], snap["total_cost"]) == (100, pytest.approx(2_000.0))
    assert incomplete == []


# --- a qty_event on a flat position ------------------------------------------

def test_a_split_credited_to_a_flat_position_is_booked_and_reported():
    """The shares are in custody, so they are booked — positions.csv must match
    the custodian. But there is no cost to give them: the history that explains
    them is missing, and both surfaces have to say so instead of silently
    holding 100 shares at zero cost."""
    pos, incomplete, snap, tl = both(("2025-01-02", "buy", "WEGE3", 100, 10.0),
                                     ("2025-02-03", "sell", "WEGE3", 100, 12.0),
                                     ("2025-03-10", "split", "WEGE3", 100, None))
    assert (pos["qty"], pos["total_cost"]) == (100, 0.0)
    assert (snap["qty"], snap["total_cost"]) == (100, 0.0)
    assert incomplete == ["WEGE3"]
    rv = apurar(tl, 2025, asset_class)
    assert avisos(rv, tl)["orfaos"] == [{"date": "2025-03-10", "ticker": "WEGE3", "qty": 100.0}]
    report: list[str] = []
    _avisos(report.append, avisos(rv, tl))
    assert "| 2025-03-10 | WEGE3 | 100 |" in report


# --- a debit as large as, or larger than, the holding ------------------------

def test_a_grupamento_debit_larger_than_the_holding_closes_it_with_no_cost_left():
    """10 held, 20 debited. positions.csv clamped the quantity and kept the
    R$ 100 of cost, which the next buy then averaged in; the report's walk went
    to −10 shares, which the next buy of 10 netted back to zero."""
    pos, incomplete, snap, tl = both(("2025-01-02", "buy", "ABCD3", 10, 10.0),
                                     ("2025-03-03", "split", "ABCD3", -20, None),
                                     ("2025-04-01", "buy", "ABCD3", 10, 12.0))
    assert (pos["qty"], pos["total_cost"]) == (10, pytest.approx(120.0))
    assert (snap["qty"], snap["total_cost"]) == (10, pytest.approx(120.0))
    assert incomplete == ["ABCD3"]
    rv = apurar(tl, 2025, asset_class)
    assert avisos(rv, tl)["orfaos"] == [{"date": "2025-03-03", "ticker": "ABCD3", "qty": -20.0}]


def test_a_debit_of_exactly_the_holding_closes_it_cleanly():
    """A conversion or an auctioned fraction debits the whole holding. That is a
    position closing, not missing history."""
    pos, incomplete, snap, tl = both(("2025-01-02", "buy", "ABCD3", 10, 10.0),
                                     ("2025-03-03", "split", "ABCD3", -10, None))
    assert pos is None and snap is None
    assert incomplete == []
    assert avisos(apurar(tl, 2025, asset_class), tl)["orfaos"] == []


def test_a_days_credit_is_walked_before_its_debit():
    """A bonus credited and a fraction debited on one date: no time of day says
    which came first. Debit-first, 10.5 off a holding of 10 reads as órfão and
    the 1 credited after it as a credit to a flat position."""
    _t, _e, tl = book(("2025-01-02", "buy", "GOAU4", 10, 9.0))
    day = pd.Timestamp("2025-06-10")
    ev = pd.DataFrame([{"ticker": "GOAU4", "date": day, "kind": "qty_event", "qty": q,
                        "amount": 0.0, "broker": "XP"} for q in (-10.5, 1.0)])
    snap = snapshot_positions(pd.concat([tl, ev], ignore_index=True), FIM).iloc[0]
    assert (snap["qty"], snap["total_cost"]) == (pytest.approx(0.5), pytest.approx(90.0))
    rv = apurar(pd.concat([tl, ev], ignore_index=True), 2025, asset_class)
    assert rv["orfaos"] == []


# --- the transition function itself ------------------------------------------

def test_posicao_never_goes_negative_and_never_keeps_cost_on_zero_shares():
    p = Posicao()
    p.comprar(10, 100.0)
    assert p.evento({"kind": "qty_event", "qty": -25.0, "amount": 0.0}) is True
    assert (p.qty, p.cost) == (0.0, 0.0)
    assert p.evento({"kind": "qty_event", "qty": 5.0, "amount": 0.0}) is True
    assert (p.qty, p.cost) == (5.0, 0.0)
    assert p.evento({"kind": "qty_event", "qty": 5.0, "amount": 0.0}) is False
    assert p.evento({"kind": "amort", "qty": 0.0, "amount": 50.0}) is False
    assert (p.qty, p.cost) == (10.0, 0.0)


# --- the vendored book, end to end -------------------------------------------

def test_positions_csv_and_bens_e_direitos_agree_on_every_vendored_ticker():
    """Same events, same walk: every ticker both surfaces hold today has the
    same quantity and cost on both. (build_timeline leaves out the renda fixa
    contracts, which renda_fixa_year_end declares from the raw extract.)"""
    positions = pd.read_csv(DATA / "positions.csv").set_index("ticker")
    snap = snapshot_positions(irpf_2026.build_timeline(), pd.Timestamp.max).set_index("ticker")
    common = positions.index.intersection(snap.index)
    assert len(common) == len(snap)
    pd.testing.assert_series_equal(positions.loc[common, "qty"], snap.loc[common, "qty"],
                                   check_dtype=False)
    pd.testing.assert_series_equal(positions.loc[common, "total_cost"],
                                   snap.loc[common, "total_cost"], check_dtype=False)


def test_realizar_still_reports_every_sale_once():
    _t, _e, tl = book(("2025-01-02", "buy", "PETR4", 100, 20.0),
                      ("2025-07-01", "sell", "PETR4", 50, 30.0))
    r = realizar(tl)
    assert r["qty"].tolist() == [50] and r["pnl"].tolist() == [pytest.approx(500.0)]
