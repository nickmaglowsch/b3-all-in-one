"""Rulers for the bench: /benchmarks and /benchmarks/series.

The compare panel could only put backtests next to other backtests. "Did this
beat the CDI?" is the question a Brazilian investor actually asks, and it has a
different shape from "did it beat my other run": the answer is not another
simulation, it is a stored series the plane already carries.

The endpoint is deliberately thin — it shells the engine's one reader
(scripts/bench_series.py) and hands back raw levels. It does not rebase, because
each series is on its own scale (IBOV in points, CDI a factor, bitcoin in reais)
and only the caller knows the grid it is drawing on.
"""

import datetime

import pytest

from platform_app import routes_benchmarks
from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _bencher(client, email: str) -> dict[str, str]:
    return await auth(client, email, ["bench"])


def _stub(monkeypatch, payload: dict, seen: list | None = None):
    """Replace the engine subprocess. The route's job is the HTTP contract —
    the reader itself is pinned by engine/tests/test_bench_series.py."""
    from platform_app import routes_benchmarks as rb

    rb.clear_cache()

    def fake(start, end, names):
        if seen is not None:
            seen.append((start, end, tuple(names)))
        return payload

    monkeypatch.setattr(rb, "_run_bench", fake)


# ── the catalogue ─────────────────────────────────────────────────────────────


async def test_the_catalogue_is_the_list_the_picker_renders(client):
    headers = await _bencher(client, "catalogue@example.com")

    r = await client.get("/benchmarks", headers=headers)

    assert r.status_code == 200, r.text
    keys = [b["key"] for b in r.json()]
    assert keys == ["cdi", "ipca", "ibov", "sp500", "bitcoin", "selic",
                    "irfm1", "irfm1p", "irfm", "irfmp2", "imab5", "imab5p",
                    "imab5p2", "imab", "imas", "imageral"]
    # Every entry carries what the UI has to render: a pt-BR label and the note
    # that says what the number actually is (a proxy, a currency, an index).
    assert all(b["label"] and b["note"] for b in r.json())


async def test_the_bench_module_gates_both_routes(client):
    """A benchmark is only reachable from the bench, so it is gated with it —
    otherwise the cheapest plan gets the engine subprocess for free."""
    headers = await auth(client, "nobench@example.com")

    assert (await client.get("/benchmarks", headers=headers)).status_code == 402
    assert (await client.get(
        "/benchmarks/series?start=2024-01-01&end=2024-12-31",
        headers=headers)).status_code == 402


# ── the series ────────────────────────────────────────────────────────────────


async def test_series_returns_raw_levels_per_requested_name(client, monkeypatch):
    headers = await _bencher(client, "series@example.com")
    seen: list = []
    _stub(monkeypatch, {"cdi": {"2024-01-02": 1.5}, "ibov": {"2024-01-02": 130000.0},
                        "offline": False}, seen)

    r = await client.get(
        "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=cdi,ibov",
        headers=headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["series"]["cdi"] == {"2024-01-02": 1.5}
    assert body["series"]["ibov"] == {"2024-01-02": 130000.0}
    assert body["unavailable"] == []
    assert seen == [("2024-01-01", "2024-01-31", ("cdi", "ibov"))]


async def test_a_line_drawn_off_the_proxy_says_so(client, monkeypatch):
    """sp500 is `$SPX`; when the plane filled it (or its tail) from SPY, the
    response says which line and from when, for the chart to tell the reader."""
    headers = await _bencher(client, "proxied@example.com")
    _stub(monkeypatch, {"sp500": {"2024-01-02": 5000.0}, "cdi": {"2024-01-02": 1.5},
                        "proxied": {"sp500": {"proxy": "SPY", "from": "2024-01-02"},
                                    "bitcoin": {"proxy": "X", "from": None}},
                        "offline": False})
    r = await client.get(
        "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=sp500,cdi",
        headers=headers)
    assert r.status_code == 200, r.text
    # Only for lines actually served.
    assert r.json()["proxied"] == {"sp500": {"proxy": "SPY", "from": "2024-01-02"}}


async def test_a_name_the_plane_cannot_serve_is_named_not_dropped(client, monkeypatch):
    """A benchmark that silently vanishes from a chart is worse than one the
    page says it could not draw — the reader has no way to tell the difference
    between "not selected" and "no data"."""
    headers = await _bencher(client, "gap@example.com")
    _stub(monkeypatch, {"cdi": {"2024-01-02": 1.5}, "bitcoin": {}, "offline": False})

    r = await client.get(
        "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=cdi,bitcoin",
        headers=headers)

    assert r.json()["unavailable"] == ["bitcoin"]
    assert "bitcoin" not in r.json()["series"]


async def test_an_unknown_key_is_a_422_not_an_empty_chart(client, monkeypatch):
    headers = await _bencher(client, "unknown@example.com")
    _stub(monkeypatch, {"offline": True})

    r = await client.get(
        "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=ibovespa",
        headers=headers)

    assert r.status_code == 422
    assert "ibovespa" in r.text


async def test_an_inverted_window_is_refused(client, monkeypatch):
    headers = await _bencher(client, "inverted@example.com")
    _stub(monkeypatch, {"offline": True})

    r = await client.get(
        "/benchmarks/series?start=2024-12-31&end=2024-01-01&keys=cdi",
        headers=headers)

    assert r.status_code == 422


async def test_an_engine_failure_degrades_rather_than_500s(client, monkeypatch):
    """A benchmark is decoration on top of the runs the user selected. The same
    posture the portfolio index takes: the chart still draws."""
    from platform_app import routes_benchmarks as rb

    headers = await _bencher(client, "enginedown@example.com")
    rb.clear_cache()

    def boom(start, end, names):
        raise RuntimeError("engine venv is on fire")

    monkeypatch.setattr(rb, "_run_bench", boom)

    r = await client.get(
        "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=cdi",
        headers=headers)

    assert r.status_code == 200, r.text
    assert r.json() == {"start": "2024-01-01", "end": "2024-01-31",
                        "series": {}, "unavailable": ["cdi"], "proxied": {}}


async def test_one_window_is_read_once_per_day(client, monkeypatch):
    """These levels are the same numbers for every tenant and change once a
    day, and every open compare panel asks for them. Paid once per (window,
    keys, day) rather than once per page load."""
    headers = await _bencher(client, "cached@example.com")
    seen: list = []
    _stub(monkeypatch, {"cdi": {"2024-01-02": 1.5}, "offline": False}, seen)

    url = "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=cdi"
    assert (await client.get(url, headers=headers)).status_code == 200
    assert (await client.get(url, headers=headers)).status_code == 200

    assert len(seen) == 1


async def test_a_failure_is_not_cached(client, monkeypatch):
    """Caching "no benchmarks" would pin a momentary engine outage for the rest
    of the day over a series the plane actually has."""
    from platform_app import routes_benchmarks as rb

    headers = await _bencher(client, "notcached@example.com")
    rb.clear_cache()
    calls: list = []

    def flaky(start, end, names):
        calls.append(names)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return {"cdi": {"2024-01-02": 1.5}, "offline": False}

    monkeypatch.setattr(rb, "_run_bench", flaky)

    url = "/benchmarks/series?start=2024-01-01&end=2024-01-31&keys=cdi"
    assert (await client.get(url, headers=headers)).json()["unavailable"] == ["cdi"]
    assert (await client.get(url, headers=headers)).json()["series"]["cdi"]


async def test_the_default_window_ends_today_and_needs_no_arguments(client, monkeypatch):
    """The panel asks for the union of the selected runs' windows, but a bare
    call has to be answerable — otherwise every caller reimplements a default."""
    headers = await _bencher(client, "defaults@example.com")
    seen: list = []
    _stub(monkeypatch, {**{k: {} for k in routes_benchmarks.KEYS}, "offline": True},
          seen)

    r = await client.get("/benchmarks/series", headers=headers)

    assert r.status_code == 200, r.text
    start, end, names = seen[0]
    assert end == datetime.date.today().isoformat()
    assert names == tuple(routes_benchmarks.KEYS)
    assert r.json()["unavailable"] == routes_benchmarks.KEYS


async def test_the_cache_is_bounded(monkeypatch):
    """A process-lifetime dict keyed on the window is a slow leak otherwise:
    every distinct pair of dates a user types is a new entry that never leaves."""
    from platform_app import routes_benchmarks as rb

    rb.clear_cache()
    monkeypatch.setattr(rb, "_run_bench",
                        lambda start, end, names: {"cdi": {start: 1.0}})

    for day in range(rb._CACHE_MAX + 2):
        await rb._levels(f"2024-01-{day % 28 + 1:02d}", f"2030-{day // 28 + 1:02d}-01",
                         ["cdi"])

    assert len(rb._CACHE) <= rb._CACHE_MAX + 1
