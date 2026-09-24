"""The US security master, built from daily listing snapshots.

The assertion that matters most is a refusal: absence from one download is not
evidence a symbol delisted. A truncated response or an error page would otherwise
read as "the entire US market delisted today", and since `delisting_date` is what
the backtest universe slices by, that would silently truncate every US backtest.

Offline — the directory text is built in-process in the documented shape.
"""
from __future__ import annotations

import pytest

from b3_pipeline import storage
from us_pipeline import symbol_directory as sd
from tests.conftest import sqlite_db

HEADER = ("Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|"
          "ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|"
          "NASDAQ Symbol|NextShares")
FOOTER = "File Creation Time: 09012026 22:10|||||||||||"


def directory(symbols, etf=(), test=()) -> str:
    lines = [HEADER]
    for s in symbols:
        lines.append(
            f"Y|{s}|{s} Inc. Common Stock|Q|Q|"
            f"{'Y' if s in etf else 'N'}|100|{'Y' if s in test else 'N'}"
            f"|N|{s}|{s}|N")
    lines.append(FOOTER)
    return "\n".join(lines) + "\n"


def many(n, prefix="SYM"):
    return [f"{prefix}{i:05d}" for i in range(n)]


def _instruments(conn):
    return storage.read_sql(
        "SELECT * FROM instruments ORDER BY symbol", conn).set_index("symbol")


# ── parsing ───────────────────────────────────────────────────────────────────

def test_the_directory_parses_into_symbols():
    df = sd.parse_directory(directory(["AAPL", "MSFT"]))
    assert list(df["symbol"]) == ["AAPL", "MSFT"]
    assert df.iloc[0]["venue"] == "NASDAQ"


def test_the_file_creation_footer_is_not_a_symbol():
    df = sd.parse_directory(directory(["AAPL"]))
    assert len(df) == 1


def test_etfs_and_test_issues_are_flagged():
    df = sd.parse_directory(
        directory(["AAPL", "SPY", "ZZZT"], etf={"SPY"}, test={"ZZZT"})
    ).set_index("symbol")
    assert df.loc["SPY", "is_etf"] and not df.loc["AAPL", "is_etf"]
    assert df.loc["ZZZT", "is_test"] and not df.loc["SPY", "is_test"]


def test_a_changed_file_shape_fails_loudly():
    """Columns are read by name. A parser indexing by position would turn an
    inserted column into silently wrong data instead of an error."""
    with pytest.raises(ValueError, match="missing"):
        sd.parse_directory("Foo|Bar\nA|B\n")


# ── snapshots ─────────────────────────────────────────────────────────────────

def test_the_first_snapshot_records_everything_as_live(tmp_path):
    conn = sqlite_db(tmp_path)
    snap = sd.parse_directory(directory(many(6_000)))
    out = sd.apply_snapshot(conn, snap, "2026-01-05")
    assert out["listed"] == 6_000 and out["delisted"] == 0
    rows = _instruments(conn)
    assert rows.loc["SYM00000", "first_seen"] == "2026-01-05"
    assert rows.loc["SYM00000", "delisting_date"] is None


def test_a_symbol_that_stops_appearing_is_delisted_at_its_last_sighting(tmp_path):
    """Dated at the last day the plane can prove it traded, not at the day the
    absence was noticed."""
    conn = sqlite_db(tmp_path)
    sd.apply_snapshot(conn, sd.parse_directory(directory(many(6_000))), "2026-01-05")
    sd.apply_snapshot(conn, sd.parse_directory(directory(many(6_000))), "2026-01-06")
    out = sd.apply_snapshot(
        conn, sd.parse_directory(directory(many(6_000)[1:])), "2026-01-07")

    assert out["delisted"] == 1
    row = _instruments(conn).loc["SYM00000"]
    assert row["delisting_date"] == "2026-01-06"
    assert "symbol directory" in row["delist_reason"]


def test_first_seen_survives_later_snapshots(tmp_path):
    """The listing date is the first sighting, and a daily re-upsert must not
    keep resetting it to today."""
    conn = sqlite_db(tmp_path)
    for day in ("2026-01-05", "2026-01-06", "2026-01-07"):
        sd.apply_snapshot(conn, sd.parse_directory(directory(many(6_000))), day)
    row = _instruments(conn).loc["SYM00000"]
    assert row["first_seen"] == "2026-01-05"
    assert row["last_seen"] == "2026-01-07"


def test_a_truncated_snapshot_is_refused_rather_than_delisting_the_market(tmp_path):
    """The guard this file exists for. One bad download must not write a
    delisting date onto every US symbol — that column is what the backtest
    universe slices by."""
    conn = sqlite_db(tmp_path)
    sd.apply_snapshot(conn, sd.parse_directory(directory(many(6_000))), "2026-01-05")

    with pytest.raises(ValueError, match="truncated download"):
        sd.apply_snapshot(conn, sd.parse_directory(directory(many(3))), "2026-01-06")

    still_live = storage.read_sql(
        "SELECT COUNT(*) c FROM instruments WHERE delisting_date IS NULL",
        conn).iloc[0]["c"]
    assert still_live == 6_000


def test_nothing_is_written_when_a_snapshot_is_refused(tmp_path):
    conn = sqlite_db(tmp_path)
    with pytest.raises(ValueError):
        sd.apply_snapshot(conn, sd.parse_directory(directory(many(10))), "2026-01-05")
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM instruments", conn).iloc[0]["c"] == 0


def test_test_issues_are_recorded_and_flagged_not_dropped(tmp_path):
    """A symbol absent because it was a test issue and a symbol absent because it
    delisted must not look the same to a later reader."""
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    sd.apply_snapshot(
        conn, sd.parse_directory(directory(syms, test={syms[0]})), "2026-01-05")
    assert _instruments(conn).loc[syms[0], "is_test_issue"] == 1


def test_etfs_are_kept_as_their_own_kind(tmp_path):
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    sd.apply_snapshot(
        conn, sd.parse_directory(directory(syms, etf={syms[1]})), "2026-01-05")
    rows = _instruments(conn)
    assert rows.loc[syms[1], "kind"] == "ETF"
    assert rows.loc[syms[0], "kind"] == "EQUITY"


def test_a_delisted_symbol_that_returns_is_not_resurrected_by_accident(tmp_path):
    """Relistings happen. The row is updated in place with a fresh last_seen; the
    delisting date it already carries is the reader's business, not a reason to
    duplicate the symbol."""
    conn = sqlite_db(tmp_path)
    syms = many(6_000)
    sd.apply_snapshot(conn, sd.parse_directory(directory(syms)), "2026-01-05")
    sd.apply_snapshot(conn, sd.parse_directory(directory(syms[1:])), "2026-01-06")
    sd.apply_snapshot(conn, sd.parse_directory(directory(syms)), "2026-01-07")
    rows = _instruments(conn)
    assert len(rows) == 6_000
    assert rows.loc[syms[0], "last_seen"] == "2026-01-07"
