"""The request handlers must not do filesystem work on the event loop.

/tlh/plans and /irpf/reports each build a throwaway workspace: two copytrees, the
tenant's entire trade history and cash-event history serialised to CSV, a copy of
every uploaded xlsx, and an rmtree at the end. Only the subprocess itself was
handed to asyncio.to_thread; everything around it ran inline.

The API is a single uvicorn process with no --workers, so the event loop is the
whole server: while one request serialises a book, every other request — a health
check, someone else's portfolio — is stopped.

These measure loop responsiveness during a request rather than asserting on
implementation details, so they stay true however the work is moved off.
"""
import asyncio
import time

from functools import partial

import pytest

from platform_app.billing import SELLABLE as ALL_MODULES
from tests.conftest import auth



_auth = partial(auth, modules=ALL_MODULES)


class LoopMonitor:
    """Ticks every 5ms on the event loop and records the worst gap it sees.

    A coroutine that awaits properly lets this tick throughout. A blocking call
    inside a handler shows up directly as one long gap.
    """

    def __init__(self, interval: float = 0.005):
        self.interval = interval
        self.max_gap = 0.0
        self._task: asyncio.Task | None = None

    async def _run(self):
        last = time.perf_counter()
        while True:
            await asyncio.sleep(self.interval)
            now = time.perf_counter()
            self.max_gap = max(self.max_gap, now - last)
            last = now

    async def __aenter__(self):
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


# Generous: the point is to catch a handler that serialises a whole book inline,
# which parks the loop for hundreds of ms, not to police scheduler jitter.
#
# Doubled under coverage. This is wall-clock, and an instrumented interpreter taxes
# every line the handler executes — CI measured 424ms for an upload that takes ~170ms
# untraced, which is the tracer, not a regression. CI also sets COVERAGE_CORE=sysmon
# (PEP 669) to keep that tax small; the doubling is the belt to its braces. A handler
# that actually blocks parks the loop for seconds, so this stays a live assertion.


def _under_coverage() -> bool:
    """True when coverage is measuring this process.

    Not `sys.gettrace()`: the sysmon backend registers with PEP 669 instead of the
    trace hook, so that check reads as "no coverage" exactly where CI runs it.
    """
    try:
        import coverage
    except ImportError:
        return False
    return coverage.Coverage.current() is not None


MAX_ACCEPTABLE_STALL = 0.5 if _under_coverage() else 0.25


@pytest.mark.asyncio(loop_scope="session")
async def test_workspace_helpers_are_not_called_on_the_loop():
    """Direct assertion on the seam: the blocking helpers must be reached from a
    worker thread, never from the thread running the loop."""
    import threading

    from platform_app import b3run

    loop_thread = threading.get_ident()
    seen: list[int] = []

    real_seed = b3run.seed_workspace

    def spy(ws):
        seen.append(threading.get_ident())
        return real_seed(ws)

    b3run.seed_workspace = spy
    try:
        from platform_app.routes import _run_tlh_plan
        from platform_app.db import async_session_maker
        import uuid

        async with async_session_maker() as s:
            try:
                await _run_tlh_plan(s, uuid.uuid4(), [])
            except Exception:
                pass  # the subprocess may fail; only where seed ran matters
    finally:
        b3run.seed_workspace = real_seed

    assert seen, "seed_workspace was never called"
    assert loop_thread not in seen, (
        "seed_workspace ran on the event loop thread — the whole API stalls "
        "while a tenant's book is written to disk")


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_does_not_stall_the_loop(client):
    """POST /imports read each file fully and then wrote it with a synchronous
    write_bytes, up to 24 files of 25 MB."""
    headers = await _auth(client, "loopblock-upload@example.com")
    blob = b"x" * (2 * 1024 * 1024)
    files = [("files", (f"negociacao-2024-{i:02d}.xlsx", blob,
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")) for i in range(6)]

    async with LoopMonitor() as mon:
        r = await client.post("/imports", headers=headers, files=files)
    assert r.status_code == 200, r.text
    assert mon.max_gap < MAX_ACCEPTABLE_STALL, (
        f"POST /imports parked the event loop for {mon.max_gap*1000:.0f}ms")


@pytest.mark.asyncio(loop_scope="session")
async def test_monitor_actually_detects_a_blocked_loop():
    """Guard on the guard: a test that can never fail is worse than no test."""
    async with LoopMonitor() as mon:
        time.sleep(0.4)          # deliberately block
        await asyncio.sleep(0.01)
    assert mon.max_gap >= 0.3, mon.max_gap
