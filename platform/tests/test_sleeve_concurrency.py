"""Sleeve targets are advised one subprocess at a time.

_combined_target looped the tenant's sleeves and awaited an engine subprocess per
sleeve. Each one builds a market plane, so on a cold cache a three-sleeve
portfolio paid three full builds back to back, on GET /portfolio/target and
POST /portfolio/rebalance-plans.

They are independent by construction — each sleeve's weights depend only on its
own strategy — so the wall clock should be the slowest sleeve, not their sum.
"""
import asyncio
import re
import time
import uuid

import pytest

from platform_app import routes
from platform_app.db import async_session_maker
from platform_app.models import PortfolioSleeve


@pytest.fixture
def clear_sleeve_cache():
    routes._SLEEVE_WEIGHTS_CACHE.clear()
    yield
    routes._SLEEVE_WEIGHTS_CACHE.clear()


async def _seed_sleeves(tid, refs_pcts):
    async with async_session_maker() as s:
        for ref, pct in refs_pcts:
            s.add(PortfolioSleeve(tenant_id=tid, strategy_ref=ref, target_pct=pct))
        await s.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_sleeves_are_advised_concurrently(monkeypatch, clear_sleeve_cache):
    tid = uuid.uuid4()
    await _seed_sleeves(tid, [("A", 30.0), ("B", 30.0), ("C", 40.0)])

    DELAY = 0.30
    in_flight = 0
    peak = 0

    async def fake_advise(strategy, freq, extra_env=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(DELAY)
            return {"weights": {f"{strategy}TICK": 1.0}}
        finally:
            in_flight -= 1

    monkeypatch.setattr(routes, "_advise_strategy", fake_advise)

    async with async_session_maker() as s:
        t = time.perf_counter()
        combined, cash, _ = await routes._combined_target(s, tid)
        elapsed = time.perf_counter() - t

    assert peak == 3, f"sleeves still advised one at a time (peak in-flight={peak})"
    assert elapsed < DELAY * 2, (
        f"three {DELAY}s sleeves took {elapsed:.2f}s — looks sequential")
    # And the blend is unchanged.
    assert combined == {"ATICK": 0.30, "BTICK": 0.30, "CTICK": 0.40}
    assert cash == 0.0


@pytest.mark.asyncio(loop_scope="session")
async def test_cash_cdi_sleeve_never_shells_out(monkeypatch, clear_sleeve_cache):
    tid = uuid.uuid4()
    await _seed_sleeves(tid, [("A", 60.0), ("CASH_CDI", 40.0)])

    called: list[str] = []

    async def fake_advise(strategy, freq, extra_env=None):
        called.append(strategy)
        return {"weights": {"ATICK": 1.0}}

    monkeypatch.setattr(routes, "_advise_strategy", fake_advise)
    async with async_session_maker() as s:
        combined, cash, _ = await routes._combined_target(s, tid)

    assert called == ["A"]
    assert combined == {"ATICK": 0.60}
    assert cash == pytest.approx(0.40)


@pytest.mark.asyncio(loop_scope="session")
async def test_ticker_sleeves_are_their_own_weight_and_never_shell_out(
    monkeypatch, clear_sleeve_cache
):
    """A ticker sleeve has nothing to advise: it is {ticker: 1} at its pct, added
    to a strategy's pick of the same name (as FixedWeight does), and an off-B3
    one comes back with its market for advise_weights --markets."""
    tid = uuid.uuid4()
    async with async_session_maker() as s:
        s.add(PortfolioSleeve(tenant_id=tid, strategy_ref="A", target_pct=40))
        for ticker, market, pct in (("PETR4", "B3", 20), ("SPY", "US", 25),
                                    ("BTCUSDT", "CRYPTO", 15)):
            s.add(PortfolioSleeve(tenant_id=tid, ticker=ticker, market=market,
                                  target_pct=pct))
        await s.commit()

    called: list[str] = []

    async def fake_advise(strategy, freq, extra_env=None):
        called.append(strategy)
        return {"weights": {"PETR4": 0.5, "VALE3": 0.5}}

    monkeypatch.setattr(routes, "_advise_strategy", fake_advise)
    async with async_session_maker() as s:
        combined, cash, off_b3 = await routes._combined_target(s, tid)

    assert called == ["A"]
    assert combined == pytest.approx(
        {"PETR4": 0.40, "VALE3": 0.20, "SPY": 0.25, "BTCUSDT": 0.15})
    assert cash == 0.0
    assert off_b3 == {"SPY": "US", "BTCUSDT": "CRYPTO"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_cached_sleeve_is_not_re_advised(monkeypatch, clear_sleeve_cache):
    """The per-day cache has to keep working now that the calls are gathered."""
    tid = uuid.uuid4()
    await _seed_sleeves(tid, [("A", 50.0), ("B", 50.0)])

    calls: list[str] = []

    async def fake_advise(strategy, freq, extra_env=None):
        calls.append(strategy)
        return {"weights": {f"{strategy}TICK": 1.0}}

    monkeypatch.setattr(routes, "_advise_strategy", fake_advise)
    async with async_session_maker() as s:
        first, *_ = await routes._combined_target(s, tid)
        second, *_ = await routes._combined_target(s, tid)

    assert sorted(calls) == ["A", "B"], f"second call re-advised: {calls}"
    assert first == second


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_fundamentals_still_reports_every_affected_strategy(
    monkeypatch, clear_sleeve_cache
):
    """The 422 names the strategies that need fundamentals. Gathering must not
    lose the ones that failed after the first, nor let the exception escape."""
    from fastapi import HTTPException

    from platform_app import enginerun

    tid = uuid.uuid4()
    await _seed_sleeves(tid, [("A", 40.0), ("B", 30.0), ("C", 30.0)])

    async def fake_advise(strategy, freq, extra_env=None):
        if strategy in ("A", "C"):
            raise enginerun.EngineNeedsFundamentals(strategy, "needs fundamentals")
        return {"weights": {"BTICK": 1.0}}

    monkeypatch.setattr(routes, "_advise_strategy", fake_advise)
    async with async_session_maker() as s:
        with pytest.raises(HTTPException) as exc:
            await routes._combined_target(s, tid)

    assert exc.value.status_code == 422
    assert "'A'" in exc.value.detail and "'C'" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_no_sleeves_is_a_409(clear_sleeve_cache):
    from fastapi import HTTPException

    async with async_session_maker() as s:
        with pytest.raises(HTTPException) as exc:
            await routes._combined_target(s, uuid.uuid4())
    assert exc.value.status_code == 409


# ── What _advise_strategy actually shells ─────────────────────────────────────
# A non-default cadence reaches the engine as `--params {"rebalance_freq": ...}`;
# 'ME' (the default) passes no params at all, so the monthly path stays identical
# to a plain advise_weights call.

def _fake_run_script(calls, returncode=0, stdout='progress\n{"weights": {"X": 1.0}}\n',
                     stderr=""):
    import subprocess

    def fake(script, args, extra_env=None):
        calls.append((script, args, extra_env))
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
    return fake


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("freq, argv", [
    ("ME", ["S"]),
    ("QE", ["S", "--params", '{"rebalance_freq": "QE"}']),
])
async def test_advise_strategy_threads_the_cadence_as_params(monkeypatch, freq, argv):
    from platform_app import enginerun

    calls: list = []
    monkeypatch.setattr(enginerun, "run_script", _fake_run_script(calls))
    out = await routes._advise_strategy("S", freq, {"ENGINE_USER_SPECS_DIR": "/d"})
    assert out == {"weights": {"X": 1.0}}
    assert calls == [("advise_weights.py", argv, {"ENGINE_USER_SPECS_DIR": "/d"})]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("freq", ["ME", "W-FRI"])
async def test_advise_strategy_failures(monkeypatch, caplog, freq):
    """Exit 3 is the needs-fundamentals signal (a clean 422 upstream); any other
    non-zero exit is a 500 naming the script and a ref, while the tail of the
    engine's output — server paths and all — goes to the log under that ref."""
    from fastapi import HTTPException

    from platform_app import enginerun

    monkeypatch.setattr(enginerun, "run_script", _fake_run_script(
        [], returncode=3, stdout="",
        stderr='{"strategy": "Two-Leg", "message": "sem fundamentos"}'))
    with pytest.raises(enginerun.EngineNeedsFundamentals) as nf:
        await routes._advise_strategy("S", freq)
    assert (nf.value.strategy, nf.value.message) == ("Two-Leg", "sem fundamentos")

    monkeypatch.setattr(enginerun, "run_script", _fake_run_script(
        [], returncode=1, stdout="", stderr="Traceback: boom"))
    with pytest.raises(HTTPException) as exc:
        await routes._advise_strategy("S", freq)
    assert exc.value.status_code == 500
    assert re.fullmatch(r"advise_weights falhou \(ref ([0-9a-f]{8})\)", exc.value.detail)
    ref = exc.value.detail[-9:-1]
    assert "boom" not in exc.value.detail
    assert any(ref in r.getMessage() and "Traceback: boom" in r.getMessage()
               for r in caplog.records)
