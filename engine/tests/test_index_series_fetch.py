"""The SGS client behind $CDI / $IPCA, with the network faked.

`test_index_series.py` covers the storage side and leaves the fetch to
`index_series.demo()`, which only checks the compounding identity. What is not
covered is the part the module's own docstring calls out as dangerous: SGS caps a
daily query at ten years and throttles intermittently, so the series is fetched in
chunks with retries — and a chunk that goes missing reads as "the CDI paid nothing
that year" rather than as an error.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from b3_pipeline import index_series


class FakeSGS:
    """Stands in for requests.get. Records URLs, answers from `pages`."""

    def __init__(self, pages, fail_times=0):
        self.pages = pages          # list of payloads, one per successful call
        self.fail_times = fail_times
        self.urls: list[str] = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("SGS said no")
        page = self.pages.pop(0) if self.pages else []
        return type("R", (), {"json": staticmethod(lambda: page)})()


def payload(*pairs):
    """(dd/mm/yyyy, percent) -> the shape SGS actually returns."""
    return [{"data": d, "valor": str(v)} for d, v in pairs]


def install(monkeypatch, fake):
    monkeypatch.setattr(index_series.requests, "get", fake)
    return fake


def test_percent_per_day_is_stored_as_a_fraction(monkeypatch):
    """SGS speaks percent. Compounding 0.041957 instead of 0.00041957 turns a
    year of CDI into several orders of magnitude of it."""
    install(monkeypatch, FakeSGS([payload(("02/01/2024", "0.041957"))]))
    s = index_series.fetch_sgs(12, datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
    assert s.loc[pd.Timestamp("2024-01-02")] == pytest.approx(0.00041957)


def test_a_window_longer_than_the_query_cap_is_split_into_chunks(monkeypatch):
    fake = install(monkeypatch, FakeSGS([
        payload(("02/01/2010", "0.01")),
        payload(("02/01/2015", "0.02")),
        payload(("02/01/2020", "0.03")),
    ]))
    s = index_series.fetch_sgs(12, datetime.date(2010, 1, 1), datetime.date(2020, 6, 30))
    assert len(fake.urls) == 3
    assert list(s.index.year) == [2010, 2015, 2020]   # every chunk made it back


def test_the_chunks_tile_the_window_without_a_gap_or_an_overlap(monkeypatch):
    """A day dropped between two chunks is a day of missing CDI that nothing
    downstream would ever flag."""
    fake = install(monkeypatch, FakeSGS([[], [], []]))
    index_series.fetch_sgs(12, datetime.date(2010, 1, 1), datetime.date(2020, 6, 30))
    bounds = [(u.split("dataInicial=")[1].split("&")[0], u.split("dataFinal=")[1])
              for u in fake.urls]
    for (_, prev_end), (next_start, _) in zip(bounds, bounds[1:]):
        end = datetime.datetime.strptime(prev_end, "%d/%m/%Y").date()
        start = datetime.datetime.strptime(next_start, "%d/%m/%Y").date()
        assert start == end + datetime.timedelta(days=1)


def test_a_throttled_chunk_is_retried_rather_than_lost(monkeypatch):
    fake = install(monkeypatch, FakeSGS([payload(("02/01/2024", "0.01"))], fail_times=2))
    s = index_series.fetch_sgs(12, datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
    assert len(fake.urls) == 3      # two refusals, then the answer
    assert len(s) == 1


def test_a_chunk_that_never_answers_raises_instead_of_returning_a_hole(monkeypatch):
    install(monkeypatch, FakeSGS([payload(("02/01/2024", "0.01"))], fail_times=3))
    with pytest.raises(RuntimeError, match="SGS 12"):
        index_series.fetch_sgs(12, datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))


def test_overlapping_chunks_do_not_double_count_a_day(monkeypatch):
    install(monkeypatch, FakeSGS([
        payload(("02/01/2015", "0.01"), ("03/01/2015", "0.02")),
        payload(("03/01/2015", "0.02"), ("04/01/2015", "0.03")),
        payload(),
    ]))
    s = index_series.fetch_sgs(12, datetime.date(2010, 1, 1), datetime.date(2020, 6, 30))
    assert s.index.is_unique and len(s) == 3


def test_a_series_with_no_data_at_all_comes_back_empty(monkeypatch):
    install(monkeypatch, FakeSGS([[]]))
    assert index_series.fetch_sgs(433, datetime.date(2024, 1, 1),
                                  datetime.date(2024, 1, 31)).empty


# --------------------------------------------------------------------------
# build — rates to stored levels
# --------------------------------------------------------------------------

def rates_for(monkeypatch, **by_ticker):
    """Replace fetch_sgs so build() sees exactly these rates per series."""
    by_series = {index_series.CDI_SERIES: by_ticker.get("cdi"),
                 index_series.IPCA_SERIES: by_ticker.get("ipca"),
                 index_series.SELIC_SERIES: by_ticker.get("selic")}

    def fake(series, start, end):
        got = by_series[series]
        if isinstance(got, Exception):
            raise got
        return got if got is not None else pd.Series(dtype=float)

    monkeypatch.setattr(index_series, "fetch_sgs", fake)


def series(*pairs) -> pd.Series:
    return pd.Series([v for _, v in pairs],
                     index=pd.to_datetime([d for d, _ in pairs]))


def test_the_stored_close_is_a_cumulative_level_not_the_daily_rate(monkeypatch):
    """Growth between any two dates has to be one division. Storing rates would
    make it depend on where the caller's window happens to start."""
    rates_for(monkeypatch, cdi=series(("2024-01-02", 0.01), ("2024-01-03", 0.02)))
    df = index_series.build(datetime.date(2024, 1, 31))
    cdi = df[df["ticker"] == index_series.CDI_TICKER]
    assert list(cdi["close"]) == pytest.approx([1.01, 1.01 * 1.02])


def test_a_rate_row_carries_no_range_and_no_volume(monkeypatch):
    """The containment check in upsert_external_prices compares close against low
    before it looks at notna(), so these have to be NaN and not None."""
    rates_for(monkeypatch, cdi=series(("2024-01-02", 0.01)))
    df = index_series.build(datetime.date(2024, 1, 31))
    row = df.iloc[0]
    assert pd.isna(row["open"]) and pd.isna(row["high"]) and pd.isna(row["low"])
    assert row["volume"] == 0.0 and row["source"] == index_series.SOURCE
    for col in ("open", "high", "low"):
        assert df[col].dtype.kind == "f", f"{col} inferred object dtype"


def test_one_unreachable_series_does_not_take_the_other_down(monkeypatch):
    """A book holding only CDI-indexed paper does not care that IPCA was down."""
    rates_for(monkeypatch, cdi=series(("2024-01-02", 0.01)),
              ipca=RuntimeError("SGS 433 down"))
    df = index_series.build(datetime.date(2024, 1, 31))
    assert set(df["ticker"]) == {index_series.CDI_TICKER}


def test_an_empty_series_is_skipped_rather_than_stored_as_a_flat_line(monkeypatch):
    rates_for(monkeypatch, cdi=series(("2024-01-02", 0.01)), ipca=pd.Series(dtype=float))
    df = index_series.build(datetime.date(2024, 1, 31))
    assert set(df["ticker"]) == {index_series.CDI_TICKER}


def test_both_series_down_yields_an_empty_frame_not_a_crash(monkeypatch):
    rates_for(monkeypatch, cdi=RuntimeError("down"), ipca=RuntimeError("down"))
    assert index_series.build(datetime.date(2024, 1, 31)).empty


def test_the_module_self_check_still_holds():
    """`demo()` is the compounding identity the storage format rests on — levels
    compound, a 0% day is flat, and dividing consecutive levels gives the rate
    back. It is only a self-check if something runs it."""
    index_series.demo()
