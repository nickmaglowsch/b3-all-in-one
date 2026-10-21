"""The two event timelines the cost-basis walks consume, pinned frame-for-frame.

`load_b3.derive_positions` and `irpf_2026.build_timeline` each merge trades and
corporate-action events into one chronological frame, then walk it in preço
médio. Within one date the walk depends on row order, so the frame itself —
values, dtypes, column order, row order, index — is the contract. Each test runs
the production builder against a row-by-row reference (the original iterrows
construction, kept here as the oracle) on the vendored fixtures and on a small
book of edge cases: same-day ties, NaN quantities and amounts, *F tickers, a
cash_events file old enough to lack `event_category`, a bonificação whose
custo atribuído (qty × price) is the one cost a qty_event carries.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import irpf_2026, load_b3
from scripts.load_b3 import _FRACTIONAL_RE, _ROUND_LOT, _position_timeline

DATA = Path(__file__).resolve().parents[1] / "data"


# --- oracles: the original row-by-row construction --------------------------

def _custo(r):
    """Cost a qty_event brings in: qty × price, 0 where either is missing."""
    qty, price = r.get("qty"), r.get("price")
    return float(qty) * float(price) if pd.notna(qty) and pd.notna(price) else 0.0


def reference_position_timeline(trades, cash_events, consolidate):
    t = trades.copy()
    if consolidate:
        t["ticker"] = t["ticker"].astype(str).str.replace(
            _FRACTIONAL_RE, _ROUND_LOT, regex=True)
    rows = []
    for _, r in t.sort_values("date").iterrows():
        rows.append({"ticker": r["ticker"], "date": pd.to_datetime(r["date"]),
                     "kind": "trade", "sq": float(r["signed_qty"]),
                     "price": float(r["price"]), "amount": 0.0,
                     "market": r["market"]})
    if cash_events is not None and not cash_events.empty:
        ce = cash_events.copy()
        if consolidate:
            ce["ticker"] = ce["ticker"].astype(str).str.replace(
                _FRACTIONAL_RE, _ROUND_LOT, regex=True)
        cat_col = ce["event_category"] if "event_category" in ce.columns else None
        if cat_col is not None:
            qty_evs = ce[cat_col == "qty_event"]
            amort_evs = ce[cat_col == "amortization"]
        else:
            qty_evs = ce.iloc[0:0]
            amort_evs = ce[ce.get("is_amortization", pd.Series(False, index=ce.index)).fillna(False)]
        for _, r in qty_evs.iterrows():
            rows.append({"ticker": r["ticker"], "date": pd.to_datetime(r["date"]),
                         "kind": "qty_event",
                         "sq": float(r["qty"]) if pd.notna(r["qty"]) else 0.0,
                         "price": 0.0, "amount": _custo(r), "market": None})
        for _, r in amort_evs.iterrows():
            rows.append({"ticker": r["ticker"], "date": pd.to_datetime(r["date"]),
                         "kind": "amort", "sq": 0.0, "price": 0.0,
                         "amount": float(r["amount"]) if pd.notna(r["amount"]) else 0.0,
                         "market": None})
    timeline = pd.DataFrame(rows).dropna(subset=["date", "ticker"])
    timeline["__order"] = timeline["kind"].map({"trade": 0, "qty_event": 1, "amort": 2})
    return timeline.sort_values(["ticker", "date", "__order"]).reset_index(drop=True)


def reference_build_timeline():
    trades = pd.read_csv(irpf_2026.TRADES, parse_dates=["date"])
    events = pd.read_csv(irpf_2026.EVENTS, parse_dates=["date"])
    trades = trades[trades["market"].astype(str).str.contains(
        r"Variável|à Vista|Vista|Fracionário|Renda Fixa", case=False, na=False,
        regex=True)].copy()
    is_rf = trades["market"].astype(str).str.contains("Renda Fixa", case=False, na=False)
    valid = trades["ticker"].astype(str).apply(lambda s: bool(load_b3._TICKER_RE.match(s)))
    trades = trades[~is_rf | (is_rf & valid)].copy()
    trades["ticker_root"] = trades["ticker"].str.replace(
        _FRACTIONAL_RE, _ROUND_LOT, regex=True)
    trades["is_buy"] = trades["side"].str.lower().str.startswith("compra")
    events = events[~events["ticker"].astype(str).str.match(
        irpf_2026.RF_PREFIX_RE.pattern, case=False, na=False)].copy()
    rows = []
    for _, t in trades.iterrows():
        rows.append({"ticker": t["ticker_root"], "date": t["date"],
                     "kind": "buy" if t["is_buy"] else "sell",
                     "qty": float(t["qty"]),
                     "amount": float(t["qty"]) * float(t["price"]),
                     "broker": t.get("broker", "")})
    for _, e in events.iterrows():
        cat = e.get("event_category")
        if cat == "qty_event":
            rows.append({"ticker": str(e["ticker"]), "date": e["date"], "kind": "qty_event",
                         "qty": float(e["qty"]) if pd.notna(e["qty"]) else 0.0,
                         "amount": _custo(e), "broker": e.get("broker", "")})
        elif cat == "amortization":
            rows.append({"ticker": str(e["ticker"]), "date": e["date"], "kind": "amort",
                         "qty": 0.0,
                         "amount": float(e["amount"]) if pd.notna(e["amount"]) else 0.0,
                         "broker": e.get("broker", "")})
    return pd.DataFrame(rows).sort_values(["ticker", "date"]).reset_index(drop=True)


# --- inputs -------------------------------------------------------------------

def vendored():
    return (pd.read_csv(DATA / "trades.csv", parse_dates=["date"]),
            pd.read_csv(DATA / "cash_events.csv", parse_dates=["date"]))


D1, D2 = pd.Timestamp("2025-03-10"), pd.Timestamp("2025-03-11")

EDGE_TRADES = pd.DataFrame({
    "date": [D2, D1, D1, D1, pd.NaT, D2],
    "side": ["Venda", "Compra", "Compra", "Compra", "Compra", "Compra"],
    "market": ["Mercado à Vista", "Mercado Fracionário", "Mercado à Vista",
               "Renda Fixa", "Mercado à Vista", "Mercado à Vista"],
    "maturity": "-",
    "broker": ["XP", "XP", np.nan, "XP", "XP", "XP"],
    "ticker": ["PETR4", "PETR4F", "PETR4", "LFTB11", "VALE3", "LCI - LCI24H9RKF"],
    "qty": [50.0, 7.0, 100.0, 3.0, 1.0, 1.0],
    "price": [31.0, 30.5, 30.0, 110.0, 60.0, 1000.0],
    "amount": [1550.0, 213.5, 3000.0, 330.0, 60.0, 1000.0],
    "signed_qty": [-50.0, 7.0, 100.0, 3.0, 1.0, 1.0],
    "__source_file": "edge.xlsx",
})

EDGE_EVENTS = pd.DataFrame({
    "date": [D1, D1, D2, D1, D2, D2, D1, D2],
    "ticker": ["PETR4", "PETR4F", "PETR4", "PETR4", "LCI - LCI24H9RKF", "VALE3",
               np.nan, "PETR4"],
    "event_type": ["Desdobro", "Amortização", "Fração em Ativos", "Dividendo",
                   "Amortização", "Desdobro", "Amortização", "Bonificação em Ativos"],
    "event_category": ["qty_event", "amortization", "qty_event", "income",
                       "amortization", "qty_event", "amortization", "qty_event"],
    "is_amortization": [False, True, False, False, True, np.nan, True, False],
    "qty": [100.0, 0.0, np.nan, 0.0, 0.0, -1.0, 0.0, 20.0],
    "price": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 8.9],
    "amount": [0.0, 12.5, 0.0, 40.0, np.nan, 0.0, 3.0, 0.0],
    "broker": "XP",
    "__source_file": "edge.xlsx",
})


# --- load_b3.derive_positions ----------------------------------------------

@pytest.mark.parametrize("consolidate", [True, False])
@pytest.mark.parametrize("book", ["vendored", "edge", "edge_legacy", "edge_no_events"])
def test_position_timeline_matches_row_by_row_reference(book, consolidate):
    trades, events = vendored() if book == "vendored" else (EDGE_TRADES, EDGE_EVENTS)
    if book == "edge_legacy":
        events = events.drop(columns="event_category")
    elif book == "edge_no_events":
        events = None
    pd.testing.assert_frame_equal(
        _position_timeline(trades, events, consolidate),
        reference_position_timeline(trades, events, consolidate))


# --- irpf_2026.build_timeline ------------------------------------------------

@pytest.fixture
def edge_csvs(tmp_path, monkeypatch):
    def write(trades, events):
        trades.to_csv(tmp_path / "trades.csv", index=False)
        events.to_csv(tmp_path / "cash_events.csv", index=False)
        monkeypatch.setattr(irpf_2026, "TRADES", tmp_path / "trades.csv")
        monkeypatch.setattr(irpf_2026, "EVENTS", tmp_path / "cash_events.csv")
    return write


def test_build_timeline_matches_row_by_row_reference_on_vendored_data():
    pd.testing.assert_frame_equal(irpf_2026.build_timeline(), reference_build_timeline())


@pytest.mark.parametrize("drop", [None, "broker", "event_category"])
def test_build_timeline_matches_row_by_row_reference_on_edge_cases(edge_csvs, drop):
    trades, events = EDGE_TRADES, EDGE_EVENTS
    if drop == "broker":
        trades = trades.drop(columns="broker")
    elif drop == "event_category":  # a cash_events.csv older than the column
        events = events.drop(columns="event_category")
    edge_csvs(trades, events)
    pd.testing.assert_frame_equal(irpf_2026.build_timeline(), reference_build_timeline())
