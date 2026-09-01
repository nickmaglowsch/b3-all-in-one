"""Tests for the per-market plane summary behind GET /market/status.

A fake asyncpg pool, so these run without a database. The point of the module is
what it *reports* — coverage and gaps — and that is pure shaping of query rows.
"""
import datetime

import pytest

from platform_app import market_planes


class _Pool:
    """Answers by SQL fingerprint. Any query listed in `fails` raises, which is
    how a plane created by an older engine (no `instruments` table) behaves."""

    def __init__(self, rows=None, fails=()):
        self.rows = rows or {}
        self.fails = fails

    def _key(self, sql):
        for k in ("FROM prices", "FROM market_prices", "FROM instruments",
                  "FROM corporate_actions", "FROM fundamentals_pit_us",
                  "FROM fundamentals_pit"):
            if k in sql:
                return k
        raise AssertionError(f"unexpected query: {sql}")

    async def fetchrow(self, sql):
        key = self._key(sql)
        if key in self.fails:
            raise RuntimeError("relation does not exist")
        return self.rows.get(key)

    async def fetch(self, sql):
        key = self._key(sql)
        if key in self.fails:
            raise RuntimeError("relation does not exist")
        return self.rows.get(key, [])


D = datetime.date(2020, 8, 11)
_B3 = {"rows": 3, "tickers": 2, "min_date": D, "max_date": D}
_TBL = {"rows": 1, "fresh": D}


def _pool(**over):
    rows = {"FROM prices": _B3, "FROM corporate_actions": _TBL,
            "FROM fundamentals_pit": _TBL, "FROM fundamentals_pit_us": _TBL,
            "FROM instruments": [], "FROM market_prices": []}
    rows.update(over)
    return _Pool(rows)


@pytest.mark.asyncio
async def test_dates_are_serialised_and_nulls_survive():
    out = await market_planes.status(_pool(
        **{"FROM prices": {"rows": 0, "tickers": 0,
                           "min_date": None, "max_date": None}}))
    assert out["prices"] == {"rows": 0, "tickers": 0,
                             "min_date": None, "max_date": None}


@pytest.mark.asyncio
async def test_a_missing_table_is_absent_not_an_outage():
    """A plane created by an older engine has no fundamentals_pit_us. That is
    "not present", and it must not take the whole status response down."""
    pool = _Pool({"FROM prices": _B3, "FROM corporate_actions": _TBL,
                  "FROM fundamentals_pit": _TBL,
                  "FROM instruments": [], "FROM market_prices": []},
                 fails=("FROM fundamentals_pit_us",))
    out = await market_planes.status(pool)
    assert "fundamentals_pit_us" not in out["tables"]
    assert out["tables"]["corporate_actions"]["rows"] == 1


@pytest.mark.asyncio
async def test_the_delisted_count_is_reported_per_market():
    """The survivorship number. Zero here after a real backfill means the plane
    is dropping its dead somewhere upstream, and a reader has to be able to see
    that without querying the database themselves."""
    out = await market_planes.status(_pool(**{
        "FROM instruments": [{"market": "CRYPTO", "instruments": 3695,
                              "delisted": 1200, "first_seen": D, "last_seen": D}],
        "FROM market_prices": [{"market": "CRYPTO", "rows": 10, "min_date": D,
                                "max_date": D, "unconverted": 0}],
    }))
    assert out["markets"]["CRYPTO"]["delisted"] == 1200
    assert out["markets"]["CRYPTO"]["instruments"] == 3695


@pytest.mark.asyncio
async def test_unconverted_bars_are_reported_not_hidden():
    """Bars with no PTAX for their date, or a quote asset with no route to the
    real, have NULL BRL columns. That is a gap the product states out loud."""
    out = await market_planes.status(_pool(**{
        "FROM instruments": [{"market": "CRYPTO", "instruments": 1, "delisted": 0,
                              "first_seen": D, "last_seen": D}],
        "FROM market_prices": [{"market": "CRYPTO", "rows": 10, "min_date": D,
                                "max_date": D, "unconverted": 4}],
    }))
    assert out["markets"]["CRYPTO"]["unconverted_brl"] == 4


@pytest.mark.asyncio
async def test_a_market_with_instruments_but_no_prices_still_appears():
    """A real state during a first backfill. Vanishing would read as "no such
    market" when the truth is "enumerated, not yet fetched"."""
    out = await market_planes.status(_pool(**{
        "FROM instruments": [{"market": "US", "instruments": 40, "delisted": 2,
                              "first_seen": None, "last_seen": None}],
    }))
    assert out["markets"]["US"]["price_rows"] == 0
    assert out["markets"]["US"]["min_date"] is None


@pytest.mark.asyncio
async def test_no_instruments_table_yields_an_empty_market_block():
    pool = _Pool({"FROM prices": _B3, "FROM corporate_actions": _TBL,
                  "FROM fundamentals_pit": _TBL, "FROM fundamentals_pit_us": _TBL},
                 fails=("FROM instruments", "FROM market_prices"))
    assert (await market_planes.status(pool))["markets"] == {}
