"""Tests for the USD/BRL series that materialises the BRL price columns."""
from __future__ import annotations

import datetime

import pandas as pd

from b3_pipeline import fx_series


def _rates(pairs):
    return pd.Series([v for _, v in pairs],
                     index=pd.to_datetime([d for d, _ in pairs]))


PTAX = _rates([("2024-01-02", 4.90), ("2024-01-03", 5.00), ("2024-01-05", 5.10)])


# ── as_of ─────────────────────────────────────────────────────────────────────

def test_a_published_day_uses_its_own_rate():
    got = fx_series.as_of(PTAX, ["2024-01-03"])
    assert got.iloc[0] == 5.00


def test_a_non_business_day_carries_the_last_published_rate():
    """PTAX is business-day only; crypto trades every day. A Saturday BRL mark
    means Friday's rate, which is what a holder could actually have converted at.
    """
    got = fx_series.as_of(PTAX, ["2024-01-04", "2024-01-06", "2024-01-07"])
    assert list(got) == [5.00, 5.10, 5.10]


def test_a_date_before_the_first_rate_is_not_converted():
    """Never back-fill. Pricing a 2010 bar off a 2024 rate would be inventing a
    number, and a NULL BRL close is the honest answer where the rate is unknown.
    """
    got = fx_series.as_of(PTAX, ["2023-12-31"])
    assert pd.isna(got.iloc[0])


def test_no_rates_at_all_yields_all_nan():
    got = fx_series.as_of(pd.Series(dtype=float), ["2024-01-03"])
    assert len(got) == 1 and pd.isna(got.iloc[0])


def test_duplicate_rate_dates_keep_the_last():
    dup = _rates([("2024-01-02", 4.90), ("2024-01-02", 4.95)])
    assert fx_series.as_of(dup, ["2024-01-02"]).iloc[0] == 4.95


# ── to_brl ────────────────────────────────────────────────────────────────────

def _bars():
    return pd.DataFrame({
        "date": ["2024-01-03", "2024-01-04"],
        "close": [10.0, 20.0],
        "adj_close": [10.0, 20.0],
    })


def test_brl_columns_are_the_native_price_times_the_stored_rate():
    out = fx_series.to_brl(_bars(), PTAX)
    assert list(out["fx_rate"]) == [5.00, 5.00]
    assert list(out["close_brl"]) == [50.0, 100.0]
    assert list(out["adj_close_brl"]) == [50.0, 100.0]


def test_a_brl_figure_never_appears_without_its_rate():
    """The invariant a reader relies on: close_brl IS NOT NULL AND fx_rate IS
    NULL must never occur, so any BRL number on the plane is traceable."""
    bars = _bars()
    bars.loc[len(bars)] = ["2023-06-01", 30.0, 30.0]   # before the first rate
    out = fx_series.to_brl(bars, PTAX)
    orphan = out["close_brl"].notna() & out["fx_rate"].isna()
    assert not orphan.any()
    assert pd.isna(out.iloc[-1]["close_brl"])


def test_empty_frame_passes_through():
    assert fx_series.to_brl(pd.DataFrame(), PTAX).empty


# ── build ─────────────────────────────────────────────────────────────────────

def test_build_uses_scale_one_because_ptax_is_a_price(monkeypatch):
    """CDI and IPCA publish percentages and are divided by 100. PTAX publishes
    BRL per USD. Reusing the default scale would store the dollar at 5 centavos.
    """
    seen = {}

    def fake(series, start, end, scale=100.0):
        seen["series"], seen["scale"] = series, scale
        return _rates([("2024-01-02", 4.90)])

    monkeypatch.setattr(fx_series, "fetch_sgs", fake)
    df = fx_series.build(datetime.date(2024, 1, 31))
    assert seen == {"series": 1, "scale": 1.0}
    assert df.iloc[0]["close"] == 4.90
    assert df.iloc[0]["ticker"] == "$USDBRL"


def test_build_degrades_to_empty_when_sgs_is_down(monkeypatch):
    """The caller's real work is ingesting native prices. An exception here would
    throw that away over a rate that the next run can fill in."""
    def boom(*a, **k):
        raise RuntimeError("SGS down")

    monkeypatch.setattr(fx_series, "fetch_sgs", boom)
    assert fx_series.build(datetime.date(2024, 1, 31)).empty


def test_build_reserves_a_dollar_sign_ticker():
    """`$` matches no B3 code by shape, which is why $CDI and $IPCA live in the
    same table without a collision. $USDBRL joins them on the same terms."""
    assert fx_series.USDBRL_TICKER.startswith("$")
