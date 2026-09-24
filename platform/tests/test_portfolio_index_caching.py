"""GET /portfolio/index rebuilt everything on every page load.

Per request it: loaded whole Trade and CashEvent entities for the tenant, ran
`SELECT DISTINCT date FROM prices` over the plane to rediscover the exchange
calendar, fetched multi-year closes, and shelled into the engine for IBOV and CDI
(a subprocess that imports pandas and hits Yahoo/BCB).

The trading-day grid and the benchmark series are properties of the market, not
of the tenant — they change once a day at most, and every tenant asking for them
gets the same answer.
"""
import datetime

import pytest

from platform_app import routes_portfolio_index as idx


@pytest.fixture(autouse=True)
def clear_caches():
    idx._GRID_CACHE.clear()
    idx._BENCH_CACHE.clear()
    yield
    idx._GRID_CACHE.clear()
    idx._BENCH_CACHE.clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_benchmarks_are_computed_once_per_window(monkeypatch):
    """The subprocess is the expensive part and its answer is the same for every
    tenant asking about the same window."""
    calls: list[tuple[str, str]] = []

    def fake_run(start, end):
        calls.append((start, end))
        return {"ibov": {start: 100.0, end: 110.0},
                "cdi": {start: 1.0, end: 1.01}, "offline": False}

    monkeypatch.setattr(idx, "_run_bench", fake_run)
    grid = [datetime.date(2024, 1, d) for d in range(2, 12)]

    first = await idx._benchmarks(grid)
    second = await idx._benchmarks(grid)

    assert len(calls) == 1, f"benchmarks re-shelled: {calls}"
    assert first == second
    assert set(first) == {"ibov", "cdi"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_different_window_is_a_different_entry(monkeypatch):
    calls = []
    monkeypatch.setattr(idx, "_run_bench", lambda s, e: (
        calls.append((s, e)) or {"ibov": {s: 1.0}, "cdi": {s: 1.0}, "offline": False}))

    await idx._benchmarks([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)])
    await idx._benchmarks([datetime.date(2024, 1, 2), datetime.date(2024, 6, 3)])
    assert len(calls) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_bench_run_is_not_cached(monkeypatch):
    """A transient engine failure must not pin "no benchmarks" for the day —
    same posture as the market-status cache, which deliberately skips caching an
    unreachable plane."""
    attempts = []

    def flaky(start, end):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("engine down")
        return {"ibov": {start: 1.0}, "cdi": {start: 1.0}, "offline": False}

    monkeypatch.setattr(idx, "_run_bench", flaky)
    grid = [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]

    assert await idx._benchmarks(grid) == {}
    assert await idx._benchmarks(grid) != {}
    assert len(attempts) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_an_offline_window_is_not_cached(monkeypatch):
    """offline=true means "we did not fetch", not "the benchmark is flat"."""
    attempts = []

    def offline_then_live(start, end):
        attempts.append(1)
        if len(attempts) == 1:
            return {"ibov": {}, "cdi": {}, "offline": True}
        return {"ibov": {start: 1.0}, "cdi": {start: 1.0}, "offline": False}

    monkeypatch.setattr(idx, "_run_bench", offline_then_live)
    grid = [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]
    assert await idx._benchmarks(grid) == {}
    assert await idx._benchmarks(grid) != {}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.market
async def test_trading_grid_is_fetched_once_per_start_date(monkeypatch):
    """`SELECT DISTINCT date FROM prices` is an index-only scan over most of the
    table; the exchange calendar it rediscovers changes once a day."""
    real = idx._fetch_grid
    calls = []

    async def counting(pool, start):
        calls.append(start)
        return await real(pool, start)

    monkeypatch.setattr(idx, "_fetch_grid", counting)
    start = datetime.date(2024, 1, 1)

    a = await idx._trading_grid(start)
    b = await idx._trading_grid(start)
    assert len(calls) == 1, f"grid re-queried: {calls}"
    assert a == b


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_grid_short_circuits_without_shelling(monkeypatch):
    called = []
    monkeypatch.setattr(idx, "_run_bench", lambda s, e: called.append(1))
    assert await idx._benchmarks([]) == {}
    assert not called


def test_bounded_drops_the_lot_only_past_the_cap():
    cache = {1: "a", 2: "b"}
    assert idx.bounded(cache, 2) is cache and cache == {1: "a", 2: "b"}
    cache[3] = "c"
    assert idx.bounded(cache, 2) is cache and cache == {}
