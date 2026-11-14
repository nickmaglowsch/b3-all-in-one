"""ANBIMA's index answer, and the renda-fixa ETF series spliced from it.

The parser runs on inline bytes shaped like ANBIMA's answer (latin-1, `;`,
`.` thousands, `,` decimals). The splice runs on a temporary SQLite plane. No
network anywhere.
"""
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import anbima_series, config, storage
from b3_pipeline.external_prices import FetchFailed
from backtests.core.data import download_benchmark

_ANSWER = (
    "TOTAIS - QUADRO RESUMO\r\n"
    "Índice;Data de Referência;Número Índice;Variação Diária(%);Peso(%)\r\n"
    "IRF-M 1+;30/10/2026;25.000,021503;-0,2566;15,56\r\n"
    "IMA-B 5;30/10/2026;11.912,742845;-0,0608;7,95\r\n"
    "IMA-GERAL ex-C;30/10/2026;10.420,471355;-0,0881;98,97\r\n"
).encode("latin-1")


def test_the_answer_parses_to_our_tickers_with_comma_decimals():
    df = anbima_series.parse(_ANSWER)
    assert list(df["ticker"]) == ["$IRFM1P", "$IMAB5"]      # ex-C is skipped
    assert list(df["close"]) == [25000.021503, 11912.742845]
    assert (df["date"] == pd.Timestamp("2026-10-30")).all()


def test_no_data_is_an_empty_answer_not_a_failure():
    """A holiday, a weekend or a P2 date past ANBIMA's window. The date in the
    sentence is not always the one asked for, so it is not read."""
    body = "Não há dados disponíveis para [29/10/2026] !\r\n".encode("latin-1")
    assert anbima_series.parse(body).empty


def test_an_unrecognised_answer_is_a_failure_not_a_holiday():
    """Stored as nothing, an error page would never be asked for again."""
    with pytest.raises(FetchFailed):
        anbima_series.parse(b"<html>Service Unavailable</html>")


# --- splice ---------------------------------------------------------------

def _plane(tmp_path, monkeypatch) -> str:
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)
    return db


def _external(db: str, ticker: str, rows: dict) -> None:
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO external_prices (ticker, isin_code, date, close, volume, source)"
        " VALUES (?, 'UNKNOWN', ?, ?, 0, 'TEST')",
        [(ticker, d, v) for d, v in rows.items()])
    conn.commit()
    conn.close()


_DAYS = ["2025-05-27", "2025-05-28", "2025-05-29", "2025-05-30", "2025-06-02",
         "2025-06-03"]


def test_imab11_before_its_first_close_is_imab_net_of_fee(tmp_path, monkeypatch):
    db = _plane(tmp_path, monkeypatch)
    _external(db, "$IMAB", dict(zip(_DAYS, [100.0, 101.0, 101.0, 102.0, 103.0, 104.0])))
    _external(db, "IMAB11", {"2025-06-02": 50.0, "2025-06-03": 50.2})

    px = download_benchmark("IMAB11.SA", "2025-05-01", "2025-06-30")
    drag = (1 - config.RF_ETF_INDEX["IMAB11"].fee) ** (1 / 252)
    assert list(px.index.strftime("%Y-%m-%d")) == _DAYS
    # The ETF's own closes are untouched; the anchor is its first one.
    assert px["2025-06-02"] == 50.0 and px["2025-06-03"] == 50.2
    # Continuity: the day before the anchor is the anchor discounted by the
    # index's anchor-day return and one day of fee.
    assert px["2025-05-30"] == pytest.approx(50.0 / ((103 / 102) * drag))
    # A flat index day still costs the fee: the fund charges whatever the market does.
    assert px["2025-05-29"] / px["2025-05-28"] == pytest.approx(drag)
    # Fee drag compounds: four index days net of fee, end to end.
    assert px["2025-06-02"] / px["2025-05-27"] == pytest.approx(103 / 100 * drag ** 4)


def test_an_index_that_stops_short_of_the_first_close_is_not_bridged(
        tmp_path, monkeypatch):
    """IRF-M ends 05-28, IRFM11 starts 06-02: the step between them is a return
    nobody published, so nothing is spliced rather than a flat 0% invented."""
    db = _plane(tmp_path, monkeypatch)
    _external(db, "$IRFM", {"2025-05-27": 100.0, "2025-05-28": 101.0})
    _external(db, "IRFM11", {"2025-06-02": 80.0, "2025-06-03": 80.1})
    px = download_benchmark("IRFM11", "2025-05-01", "2025-06-30")
    assert px.index[0] == pd.Timestamp("2025-06-02")


def test_an_index_that_starts_late_leaves_the_head_to_cdi(tmp_path, monkeypatch):
    """Nothing before the index's first day: the blend parks that weight in CDI,
    as it does for any late listing."""
    db = _plane(tmp_path, monkeypatch)
    _external(db, "$IMAB", {"2025-05-29": 101.0, "2025-05-30": 102.0,
                            "2025-06-02": 103.0})
    _external(db, "IMAB11", {"2025-06-02": 50.0})
    px = download_benchmark("IMAB11", "2025-01-01", "2025-06-30")
    assert px.index[0] == pd.Timestamp("2025-05-29")


def test_a_window_inside_the_etf_history_is_its_own_closes(tmp_path, monkeypatch):
    db = _plane(tmp_path, monkeypatch)
    _external(db, "$IMAB", dict(zip(_DAYS, [100.0] * 6)))
    _external(db, "IMAB11", {"2025-06-02": 50.0, "2025-06-03": 50.2})
    assert download_benchmark("IMAB11", "2025-06-02", "2025-06-30").to_dict() == {
        pd.Timestamp("2025-06-02"): 50.0, pd.Timestamp("2025-06-03"): 50.2}


def test_a_window_ending_before_the_first_close_still_anchors_on_it(tmp_path, monkeypatch):
    db = _plane(tmp_path, monkeypatch)
    _external(db, "$IMAB", dict(zip(_DAYS, [100.0, 101.0, 101.0, 102.0, 103.0, 104.0])))
    _external(db, "IMAB11", {"2025-06-02": 50.0})
    full = download_benchmark("IMAB11", "2025-05-01", "2025-06-30")
    early = download_benchmark("IMAB11", "2025-05-01", "2025-05-29")
    assert early.to_dict() == full.loc[:"2025-05-29"].to_dict()


def test_no_own_close_means_no_series_not_an_index_on_its_own(tmp_path, monkeypatch):
    db = _plane(tmp_path, monkeypatch)
    _external(db, "$IMAB", dict(zip(_DAYS, [100.0] * 6)))
    assert download_benchmark("IMAB11", "2025-05-01", "2025-06-30").empty


# --- nightly resume ---------------------------------------------------------

def test_the_nightly_run_resumes_after_the_newest_day_and_pays_the_ledger(
        tmp_path, monkeypatch):
    """First run: every weekday from the start, one of them failing. Second
    run: nothing after the newest stored day is owed yet, so the only IMA
    request is the failed day the ledger remembered."""
    import datetime

    today = datetime.date.today()
    days = anbima_series.weekdays(today - datetime.timedelta(days=14), today)
    broken = days[0]
    asked, down = [], {broken}

    def fake(day, p2=False):
        asked.append((day, p2))
        if day in down:
            raise FetchFailed("ANBIMA down")
        if p2:
            return pd.DataFrame(columns=["ticker", "date", "close"])
        return pd.DataFrame({"ticker": ["$IMAB"], "date": [pd.Timestamp(day)],
                             "close": [100.0]})

    monkeypatch.setattr(anbima_series, "fetch_day", fake)
    monkeypatch.setattr(anbima_series.time, "sleep", lambda s: None)
    monkeypatch.setattr(anbima_series, "ANBIMA_START", days[0])
    conn = sqlite3.connect(str(tmp_path / "plane.sqlite"))
    storage.init_db(conn)

    anbima_series.ingest(conn)
    assert [d for d, p2 in asked if not p2] == days
    assert storage.pending_external_failures(conn, anbima_series.SOURCE_IMA) == {broken}

    asked.clear()
    down.clear()
    anbima_series.ingest(conn)
    assert [d for d, p2 in asked if not p2] == [broken]
    assert storage.pending_external_failures(conn, anbima_series.SOURCE_IMA) == set()


def test_a_dead_anbima_trips_the_breaker_instead_of_stalling_the_backfill(monkeypatch):
    import datetime

    calls = []
    good = datetime.date(2025, 1, 2)

    def fake(day, p2=False):
        calls.append(day)
        if day == good:
            return pd.DataFrame({"ticker": ["$IMAB"], "date": [pd.Timestamp(day)],
                                 "close": [1.0]})
        raise FetchFailed("timeout")

    monkeypatch.setattr(anbima_series, "fetch_day", fake)
    days = anbima_series.weekdays(good, datetime.date(2025, 6, 30))
    with pytest.raises(anbima_series.GaveUp) as e:
        anbima_series.backfill(days, pause=0)
    assert len(calls) == 1 + anbima_series.GIVE_UP_AFTER
    assert len(e.value.result.rows) == 1          # what it got is kept
    assert len(e.value.result.failed) == anbima_series.GIVE_UP_AFTER
    assert e.value.result.attempted == frozenset(calls)


def test_a_tripped_breaker_keeps_the_owed_days_it_never_asked_about(tmp_path, monkeypatch):
    """Old holes sort first in a chunk, so an outage long enough to trip the
    breaker leaves the rest of them unasked. `_store` used to clear every pending
    day of the chunk that was not in `failed` — those included — and they sit
    behind MAX(date), where the resume never looks again."""
    import datetime

    today = datetime.date.today()
    owed = anbima_series.weekdays(datetime.date(2024, 1, 1), datetime.date(2024, 3, 1))
    assert len(owed) > anbima_series.GIVE_UP_AFTER
    asked = []

    def dead(day, p2=False):
        asked.append(day)
        raise FetchFailed("timeout")

    monkeypatch.setattr(anbima_series, "fetch_day", dead)
    monkeypatch.setattr(anbima_series.time, "sleep", lambda s: None)
    conn = sqlite3.connect(str(tmp_path / "plane.sqlite"))
    storage.init_db(conn)
    storage.upsert_external_prices(conn, anbima_series.level_rows(
        ["$IMAB"], [pd.Timestamp(today)], [100.0], anbima_series.SOURCE_IMA))
    for day in owed:
        storage.record_external_failure(conn, anbima_series.SOURCE_IMA, day, "earlier outage")

    with pytest.raises(anbima_series.GaveUp):
        anbima_series._ingest_ima(conn, today)
    assert asked == owed[:anbima_series.GIVE_UP_AFTER]
    assert storage.pending_external_failures(conn, anbima_series.SOURCE_IMA) == set(owed)
