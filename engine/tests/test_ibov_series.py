"""The official Ibovespa on the plane.

Network-free: every test feeds `parse_year` the shape B3's endpoint answers
with, so the parsing that turns a day x month matrix into a dated series is
covered without depending on B3 being up.
"""

import datetime

import pandas as pd
import pytest

from b3_pipeline import ibov_series


def _payload(rows):
    """B3's shape: one row per day-of-month, one rateValue<N> per month."""
    out = []
    for day, months in rows:
        row = {"day": day}
        row.update({f"rateValue{m}": None for m in range(1, 13)})
        row.update({f"rateValue{m}": v for m, v in months.items()})
        out.append(row)
    return {"min": {}, "max": {}, "results": out}


def test_the_matrix_becomes_a_dated_series():
    series = ibov_series.parse_year(
        _payload([(2, {1: "132.696,63"}), (24, {7: "174.041,95"})]), 2024)

    assert series.to_dict() == {
        datetime.date(2024, 1, 2): 132696.63,
        datetime.date(2024, 7, 24): 174041.95,
    }


def test_points_are_points_not_cents_or_reais():
    """The unit is the index level itself. Getting this wrong by 100 would
    still draw a correctly-shaped line, because the chart rebases — which is
    exactly why it needs asserting here instead of by eye."""
    series = ibov_series.parse_year(_payload([(2, {1: "132.696,63"})]), 2024)
    assert series.iloc[0] == pytest.approx(132696.63)


def test_days_the_index_did_not_close_are_absent_not_zero():
    """Weekends, holidays, and the 31st of a 30-day month all come back null.
    A zero here would be a 100% crash on the benchmark line."""
    series = ibov_series.parse_year(
        _payload([(1, {1: "100.000,00"}), (2, {}), (31, {4: None})]), 2024)

    assert list(series.index) == [datetime.date(2024, 1, 1)]


def test_a_day_that_is_not_a_date_is_dropped_rather_than_raising():
    """B3 sends a full 31-row grid regardless of month length, so 30 February
    arrives on every request. One bad cell must not lose the year."""
    series = ibov_series.parse_year(
        _payload([(30, {2: "999,99", 6: "100.000,00"})]), 2024)

    assert list(series.index) == [datetime.date(2024, 6, 30)]


def test_an_empty_year_is_an_empty_series_not_an_error():
    assert ibov_series.parse_year({"results": []}, 2024).empty
    assert ibov_series.parse_year({}, 2024).empty


def test_build_shapes_rows_for_external_prices(monkeypatch):
    """The columns storage.upsert_external_prices requires, and the reserved
    ticker that keeps the index out of the equity namespace."""
    monkeypatch.setattr(ibov_series, "fetch_year", lambda year: _payload(
        [(2, {1: "132.696,63"}), (3, {1: "131.000,00"})]) if year == 2024
        else {"results": []})

    df = ibov_series.build(datetime.date(2024, 12, 31), start_year=2024)

    assert set(df["ticker"]) == {"$IBOV"}
    assert set(df["source"]) == {ibov_series.SOURCE}
    assert list(df["close"]) == [132696.63, 131000.0]
    assert df["date"].tolist() == [datetime.date(2024, 1, 2),
                                   datetime.date(2024, 1, 3)]
    # A level has no range and no volume; NaN rather than a faked bar, same
    # posture as index_series.build.
    assert df["open"].isna().all() and df["high"].isna().all()
    assert (df["volume"] == 0).all()


def test_a_year_b3_will_not_answer_for_drops_that_year_only(monkeypatch):
    """Thirty-odd requests per run; one refusal must not cost the other thirty.
    Same posture as index_series.build taking CDI without IPCA."""
    def flaky(year):
        if year == 2024:
            raise RuntimeError("B3 said no")
        return _payload([(2, {1: "160.538,69"})])

    monkeypatch.setattr(ibov_series, "fetch_year", flaky)

    df = ibov_series.build(datetime.date(2025, 12, 31), start_year=2024)

    assert df["date"].tolist() == [datetime.date(2025, 1, 2)]


def test_nothing_at_all_is_an_empty_frame_the_caller_can_skip(monkeypatch):
    monkeypatch.setattr(ibov_series, "fetch_year",
                        lambda year: (_ for _ in ()).throw(RuntimeError("down")))

    assert ibov_series.build(datetime.date(2024, 12, 31), start_year=2024).empty


def test_the_future_is_not_requested(monkeypatch):
    """`end` bounds the last year asked for — a run in January must not spend a
    request on a year that does not exist yet."""
    asked = []
    monkeypatch.setattr(ibov_series, "fetch_year",
                        lambda year: asked.append(year) or {"results": []})

    ibov_series.build(datetime.date(2026, 3, 1), start_year=2024)

    assert asked == [2024, 2025, 2026]


def test_rows_past_end_are_not_stored(monkeypatch):
    """The current year comes back whole. A pipeline run dated mid-year must
    not store closes it is not entitled to yet."""
    monkeypatch.setattr(ibov_series, "fetch_year", lambda year: _payload(
        [(2, {1: "160.538,69"}), (24, {7: "174.041,95"})]))

    df = ibov_series.build(datetime.date(2026, 3, 1), start_year=2026)

    assert df["date"].tolist() == [datetime.date(2026, 1, 2)]


def test_the_series_is_sorted_and_unique(monkeypatch):
    monkeypatch.setattr(ibov_series, "fetch_year", lambda year: _payload(
        [(24, {7: "174.041,95"}), (2, {1: "160.538,69"})]))

    df = ibov_series.build(datetime.date(2026, 12, 31), start_year=2026)

    assert df["date"].is_monotonic_increasing
    assert not pd.Series(df["date"]).duplicated().any()
