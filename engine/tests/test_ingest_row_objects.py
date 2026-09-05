"""The two US ingest schedulers that kept a pandas Series per symbol.

`us_prices.select_work` and `us_listing.reconcile` both start by turning what the
plane already knows into a lookup keyed by symbol:

    state = {r["symbol"]: r for _, r in cursor.iterrows()}

`iterrows` builds a fresh `pd.Series` for every row — an object with its own
Index, its own block manager and its own boxed values — and here the dict *keeps*
all of them for the length of the run. The US universe is ~13,000 symbols, so
that is 13,000 live Series against a frame that is a few hundred KB of actual
data: measured at ~19 MB retained and ~2.1 s to build, versus ~5 MB and ~0.3 s
for the same lookup over plain dicts. Both run on every daily ingest, in the same
worker whose `mem_limit` the backtests already crowd.

`to_dict("records")` gives one plain dict per row with the same keys and the same
values, and both call sites only ever do `row["col"]` / `row.get("col")`, which a
dict answers identically.

Equivalence first — a scheduler that plans different work is a worse bug than a
slow one — then the structural guard: `iterrows` must not come back.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage, us_storage
from us_pipeline import us_listing, us_prices

TODAY = datetime.date(2026, 9, 1)


def _cursor(**rows):
    return pd.DataFrame([{"symbol": s, **v} for s, v in rows.items()]) \
        if rows else pd.DataFrame(columns=us_storage.CURSOR_COLUMNS)


class _NoIterrows:
    """Fails the moment a per-row Series is built inside the block."""

    def __init__(self, monkeypatch, where):
        def refuse(_self):
            raise AssertionError(f"iterrows in {where}")
        monkeypatch.setattr(pd.DataFrame, "iterrows", refuse)


# ── us_prices.select_work ────────────────────────────────────────────────────

def test_the_cursor_lookup_reads_the_same_columns_it_always_did():
    """Every field select_work reads off a cursor row, in one plan: status,
    last_bar and actions_fetched_at, for a symbol that has each of them."""
    cursor = _cursor(
        RETRY={"status": "retry", "last_bar": "2026-08-01",
               "actions_fetched_at": "2026-08-31"},
        CURRENT={"status": "ok", "last_bar": str(TODAY),
                 "actions_fetched_at": str(TODAY)},
        BEHIND={"status": "ok", "last_bar": "2026-08-01",
                "actions_fetched_at": str(TODAY)},
        STALEACT={"status": "ok", "last_bar": str(TODAY),
                  "actions_fetched_at": "2020-01-01"},
    )
    plan = us_prices.select_work(
        ["CURRENT", "BEHIND", "STALEACT", "RETRY", "NEW"],
        cursor, budget=-1, today=TODAY)
    assert plan == [
        ("RETRY", True, False),      # retried first, actions still fresh
        ("NEW", True, True),         # never priced
        ("BEHIND", True, False),     # bars behind today
        ("STALEACT", False, True),   # only the actions have gone stale
    ]


def test_a_cursor_row_with_missing_values_plans_the_same_work():
    """A row whose last_bar/actions_fetched_at are NULL reads back as NaN from
    the plane, and NaN is the one value a dict and a Series could disagree on."""
    cursor = _cursor(A={"status": "ok", "last_bar": None,
                        "actions_fetched_at": None})
    assert us_prices.select_work(["A"], cursor, budget=-1, today=TODAY) == \
        [("A", True, True)]


def test_an_unparseable_cursor_date_is_treated_as_no_date():
    cursor = _cursor(A={"status": "ok", "last_bar": "not-a-date",
                        "actions_fetched_at": "not-a-date"})
    assert us_prices.select_work(["A"], cursor, budget=-1, today=TODAY) == \
        [("A", True, True)]


def test_an_empty_cursor_still_plans_the_whole_universe():
    assert us_prices.select_work(list("ABC"), _cursor(), budget=-1,
                                 today=TODAY) == [
        ("A", True, True), ("B", True, True), ("C", True, True)]


def test_select_work_does_not_build_a_series_per_symbol(monkeypatch):
    cursor = _cursor(
        A={"status": "ok", "last_bar": str(TODAY), "actions_fetched_at": str(TODAY)},
        B={"status": "retry", "last_bar": None, "actions_fetched_at": None},
    )
    _NoIterrows(monkeypatch, "us_prices.select_work")
    plan = us_prices.select_work(["A", "B", "C"], cursor, budget=-1, today=TODAY)
    assert plan == [("B", True, True), ("C", True, True)]


# ── us_listing.reconcile ────────────────────────────────────────────────────

def _db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "listing.sqlite"))
    storage.init_db(conn)
    return conn


def _rows(n, prefix="SYM", status="active", ipo="2000-01-01", delisted=None):
    return pd.DataFrame([{
        "symbol": f"{prefix}{i}", "name": f"{prefix} {i} Inc",
        "venue": "NASDAQ", "kind": "EQUITY",
        "ipo_date": datetime.date.fromisoformat(ipo),
        "delisting_date": datetime.date.fromisoformat(delisted) if delisted else pd.NaT,
        "status": status,
    } for i in range(n)])


def _seen_by_the_directory(conn, symbol, first, last, **extra):
    market_storage.upsert_instruments(conn, pd.DataFrame([{
        "market": "US", "symbol": symbol, "currency": "USD",
        "name": f"{symbol} Corp", "venue": "NYSE", "kind": "EQUITY",
        "first_seen": first, "last_seen": last, **extra}]))


def _instrument(conn, symbol):
    return storage.read_sql(
        "SELECT * FROM instruments WHERE market='US' AND symbol = ?",
        conn, params=(symbol,)).iloc[0]


def _active(n=us_listing.MIN_PLAUSIBLE_ACTIVE):
    return _rows(n, "ACT")


def test_reconcile_reads_every_column_of_a_known_row(tmp_path):
    """`known` is the dict under change, and `_plan`/`_resolve`/`_foreign_delisting`
    between them read last_seen, delisting_date and delist_reason off it. One
    reconciliation that exercises all three."""
    conn = _db(tmp_path)
    # Watched trading until 2011-06-10 — a claim before that is a recycled ticker.
    _seen_by_the_directory(conn, "RECYCLED", "2005-01-03", "2011-06-10")
    # Already delisted by another writer: this feed must never restate it.
    _seen_by_the_directory(conn, "FOREIGN", "2005-01-03", "2009-02-02",
                           delisting_date="2009-02-03",
                           delist_reason="symbol directory")
    # This module's own earlier claim, now contradicted by the active file.
    _seen_by_the_directory(conn, "ACT0", "2005-01-03", "2026-09-01",
                           delisting_date="2011-01-01",
                           delist_reason=us_listing.DELIST_REASON)

    delisted = pd.concat([
        _rows(1, "RECYCLED", status="delisted", delisted="2001-01-01")
        .assign(symbol="RECYCLED"),
        _rows(1, "FOREIGN", status="delisted", delisted="2015-01-01")
        .assign(symbol="FOREIGN"),
        _rows(3, "DEAD", status="delisted", delisted="2010-01-01"),
    ], ignore_index=True)

    summary = us_listing.reconcile(conn, _active(), delisted,
                                   max_new_delistings=10_000)
    assert summary["conflicts"] == ["RECYCLED"]
    assert pd.isna(_instrument(conn, "RECYCLED")["delisting_date"])
    assert str(_instrument(conn, "FOREIGN")["delisting_date"]) == "2009-02-03"
    assert pd.isna(_instrument(conn, "ACT0")["delisting_date"])
    assert summary["revived"] == 1
    assert str(_instrument(conn, "DEAD0")["delisting_date"]) == "2010-01-01"


def test_a_settlement_date_a_few_days_early_is_pulled_to_the_observed_day(tmp_path):
    conn = _db(tmp_path)
    _seen_by_the_directory(conn, "SLACK", "2005-01-03", "2011-06-10")
    delisted = _rows(1, "SLACK", status="delisted", delisted="2011-06-08") \
        .assign(symbol="SLACK")
    us_listing.reconcile(conn, _active(), delisted, max_new_delistings=10_000)
    assert str(_instrument(conn, "SLACK")["delisting_date"]) == "2011-06-10"


def test_reconcile_does_not_build_a_series_per_known_symbol(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _seen_by_the_directory(conn, "KNOWN", "2005-01-03", "2011-06-10")
    delisted = _rows(2, "DEAD", status="delisted", delisted="2010-01-01")

    _NoIterrows(monkeypatch, "us_listing.reconcile")
    summary = us_listing.reconcile(conn, _active(), delisted,
                                   max_new_delistings=10_000)
    assert summary["written"] > 0
    assert str(_instrument(conn, "DEAD0")["delisting_date"]) == "2010-01-01"


@pytest.mark.parametrize("n", [1, 3])
def test_the_lookup_holds_plain_rows_not_pandas_objects(n, tmp_path):
    """A direct read of the structure the two schedulers keep alive. `iterrows`
    would make each value a Series; the whole point of the change is that it is
    not one."""
    conn = _db(tmp_path)
    for i in range(n):
        _seen_by_the_directory(conn, f"K{i}", "2005-01-03", "2011-06-10")
    known_df = us_listing._known(conn)
    for col in ("first_seen", "last_seen", "delisting_date"):
        known_df[col] = pd.to_datetime(known_df[col], errors="coerce").dt.date
    rows = us_listing._by_symbol(known_df)
    assert len(rows) == n
    assert all(isinstance(r, dict) for r in rows.values())
    assert rows["K0"]["last_seen"] == datetime.date(2011, 6, 10)
    assert rows["K0"]["delisting_date"] is None or pd.isna(rows["K0"]["delisting_date"])
