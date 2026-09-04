"""Staying inside the container's memory budget — and failing legibly when we don't.

A backtest is the one job in this repo whose working set is set by the market it
runs against rather than by the tenant. A B3 window is ~700 names; the US plane
carries an order of magnitude more, and a 2000..today window is ~6,500 sessions.
Every daily (date x ticker) frame the builder derives is the product of those two
numbers — hundreds of MB apiece — and ``build_shared_data`` used to derive a
dozen of them unconditionally, for every strategy, whether or not the strategy
read one. That is how a US backtest reached the worker's ``mem_limit`` and came
back as ``killed by SIGKILL``.

Two things live here, and they are not the same thing:

**The budget.** ``guard()`` turns the container's cgroup ceiling into an
``RLIMIT_DATA`` on this process, a little under the real limit. Hitting a
*rlimit* raises ``MemoryError`` at the allocation that could not be served —
a Python exception, with a traceback, at a line of our code. Hitting the *cgroup*
ceiling instead gets the process SIGKILLed by the kernel: no traceback, no
stderr, exit ``-9``, and a tenant looking at a run that failed for no stated
reason. The guard does not make anything fit; it makes the failure sayable.

**The laziness.** ``LazyShared`` is the mapping ``build_shared_data`` returns.
Frames registered with ``defer()`` are built on first read and cached, so a
momentum strategy never allocates the mean-reversion panel. Nothing downstream
changes: it is a ``dict`` subclass, so ``shared["ret"]``, ``.get()``, ``in``,
``dict(shared)`` and ``{**shared}`` all behave exactly as they did — the last
two by forcing every pending frame first, which is what "give me the whole
bundle" has to mean.
"""
from __future__ import annotations

import functools
import os
import resource
import sys
from pathlib import Path

# cgroup v2 first (what every current Docker/Podman host mounts), then v1.
_CGROUP_LIMIT_FILES = (
    Path("/sys/fs/cgroup/memory.max"),
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)

# Both cgroup versions spell "no limit" as a huge number rather than an absent
# file — v2 writes the literal "max", v1 writes 2**63-1 rounded to the page size.
# Anything past a pebibyte is that sentinel, not a ceiling anyone configured.
_NO_LIMIT_BYTES = 1 << 50

# What fraction of the container's ceiling this process may claim.
#
# The error here is deliberately one-sided. Too high and the guard never trips —
# the kernel kills the cgroup first and we are back to today's bare SIGKILL,
# which `subprocs._killed` at least names. Too low and it fires on a run that had
# room, turning a good backtest into a failure. So it errs high: it is set to
# leave the parent worker its own interpreter and connection pool, and nothing
# more. Notably it does *not* divide by the worker's concurrency of two — two
# jobs colliding is exactly the case where erring high gives up the traceback
# rather than the run. A deployment that knows its own arithmetic sets
# ENGINE_BACKTEST_MEM_MB.
_DEFAULT_FRACTION = 0.8

# Never install a guard tighter than this. A budget in the low hundreds of MB
# fails during imports, which reads as "pandas is broken" rather than "the
# ceiling is too low", and no backtest of any market fits in it anyway.
_MIN_BUDGET_MB = 512


def container_limit_mb() -> int | None:
    """The cgroup memory ceiling this process runs under, in MB.

    ``None`` when there is no ceiling — an unconstrained host, a platform with no
    cgroup filesystem, or the "max" sentinel. Never raises: an unreadable cgroup
    file means we do not know the limit, which is the same answer as not having
    one, and neither is a reason to fail a backtest.
    """
    for path in _CGROUP_LIMIT_FILES:
        try:
            raw = path.read_text().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        if value <= 0 or value >= _NO_LIMIT_BYTES:
            return None
        return value // (1024 * 1024)
    return None


def budget_mb() -> int | None:
    """How much memory this process may use, in MB, or ``None`` for unbounded.

    ``ENGINE_BACKTEST_MEM_MB`` wins outright (``0`` disables the guard entirely,
    which is how a host that mmaps oddly opts out without a code change).
    Otherwise a fraction of the container ceiling, and nothing at all when there
    is no ceiling to take a fraction of.
    """
    override = os.environ.get("ENGINE_BACKTEST_MEM_MB", "").strip()
    if override:
        try:
            value = int(float(override))
        except ValueError:
            return None
        return value if value > 0 else None

    limit = container_limit_mb()
    if limit is None:
        return None
    try:
        fraction = float(os.environ.get("ENGINE_BACKTEST_MEM_FRACTION", "")
                         or _DEFAULT_FRACTION)
    except ValueError:
        fraction = _DEFAULT_FRACTION
    fraction = min(max(fraction, 0.1), 1.0)
    budget = int(limit * fraction)
    return budget if budget >= _MIN_BUDGET_MB else None


def guard(budget: int | None = None) -> int | None:
    """Cap this process's heap at ``budget`` MB (default: :func:`budget_mb`).

    Returns the cap actually installed, or ``None`` when none was. ``RLIMIT_DATA``
    rather than ``RLIMIT_AS``: since Linux 4.7 it covers anonymous mmap, which is
    where every DataFrame lives, while leaving the file-backed mappings — the
    interpreter, libstdc++, the BLAS — outside the count. ``RLIMIT_AS`` would
    charge us for those *and* for the 64 MB of address space glibc reserves per
    thread arena, so a budget set from real memory use would fire on a run that
    was never near the cgroup ceiling.

    Only ever lowers, and never past an existing hard limit: a caller that
    already has a tighter budget keeps it. Best-effort by design — a platform
    without the rlimit is a platform where we run exactly as we did before.
    """
    budget = budget_mb() if budget is None else budget
    if not budget or budget <= 0:
        return None

    limit_bytes = int(budget) * 1024 * 1024
    for name in ("RLIMIT_DATA", "RLIMIT_AS"):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            soft, hard = resource.getrlimit(which)
        except (OSError, ValueError):
            continue
        if hard != resource.RLIM_INFINITY:
            limit_bytes = min(limit_bytes, hard)
        if soft != resource.RLIM_INFINITY and soft <= limit_bytes:
            return soft // (1024 * 1024)      # already tighter than we asked for
        try:
            resource.setrlimit(which, (limit_bytes, hard))
        except (OSError, ValueError):
            continue
        return limit_bytes // (1024 * 1024)
    return None


def peak_rss_mb() -> float:
    """Peak resident set of this process, in MB.

    ``ru_maxrss`` is kilobytes on Linux and bytes on macOS; the platform check is
    the documented way to tell, and a run reported in the wrong unit by a factor
    of a thousand is worse than not reporting it.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def trace(label: str) -> None:
    """Print ``label`` and the peak RSS to stderr when ``ENGINE_MEM_TRACE=1``.

    stderr, not stdout: the runner's stdout is a JSON payload the platform parses,
    and a stray line in it is a failed run. Off by default — this is for finding
    which stage of a run grew, not for every backtest to narrate itself.
    """
    if os.environ.get("ENGINE_MEM_TRACE") == "1":
        print(f"[mem] {label}: peak RSS {peak_rss_mb():.0f} MB",
              file=sys.stderr, flush=True)


class LazyShared(dict):
    """The ``shared_data`` bundle, with the expensive frames built on demand.

    A ``dict`` in every respect a reader can observe, except that a key
    registered with :meth:`defer` holds a thunk until something asks for it.
    ``__missing__`` is what makes ``shared["autocorr_20d"]`` work; the
    iteration overrides are what make ``dict(shared)`` and ``{**shared}`` still
    return the *whole* bundle rather than only the part that happens to have
    been touched.

    That second half is not decoration. CPython's dict-merge fast path copies a
    subclass's internal table directly and skips ``keys()`` — unless the subclass
    overrides ``__iter__``, which is exactly why ``__iter__`` is overridden here.
    Removing it would make ``{**shared}`` silently drop every untouched frame.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._thunks: dict = {}

    # ── registration ─────────────────────────────────────────────────────────
    def defer(self, key: str, build) -> None:
        """Register ``key`` as built by ``build()`` on first read."""
        self._thunks[key] = build

    def defer_group(self, keys, build) -> None:
        """Register keys that are built together — one read, several frames.

        Reading any one of them runs ``build()`` once (it returns a dict keyed by
        those names), installs all of them and clears the whole group's thunks,
        so the second key is a plain dict hit. Registering them as independent
        thunks would run the read once per key instead.
        """
        keys = tuple(keys)

        def _one(key):
            values = build()
            for name in keys:
                self._thunks.pop(name, None)
                dict.__setitem__(self, name, values[name])
            return dict.__getitem__(self, key)

        for key in keys:
            self._thunks[key] = functools.partial(_one, key)

    def alias(self, key: str, target: str) -> None:
        """Register ``key`` as another name for ``target``.

        Reading it returns the *same object* the target holds, deferred or not —
        the aliases in shared_data are documented as aliases, not copies, and
        there are tests asserting identity on them.
        """
        self._thunks[key] = lambda: self[target]

    def deferred_keys(self) -> set:
        """Keys still unbuilt. For tests and for :func:`trace`-style reporting."""
        return set(self._thunks)

    # ── mapping protocol ─────────────────────────────────────────────────────
    def __missing__(self, key):
        try:
            build = self._thunks.pop(key)
        except KeyError:
            raise KeyError(key) from None
        value = build()
        self[key] = value
        return value

    def __contains__(self, key) -> bool:
        return dict.__contains__(self, key) or key in self._thunks

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def pop(self, key, *default):
        self._thunks.pop(key, None)
        return dict.pop(self, key, *default)

    def _force(self) -> None:
        """Build every pending frame. The price of asking for the whole bundle.

        Pops rather than reading through ``__missing__``: a grouped thunk installs
        its siblings' values directly, so their entries are already present and
        reading them would never retire the thunk — this loop would not end.
        """
        while self._thunks:
            key, build = self._thunks.popitem()
            if not dict.__contains__(self, key):
                dict.__setitem__(self, key, build())

    def __iter__(self):
        self._force()
        return dict.__iter__(self)

    def __len__(self) -> int:
        self._force()
        return dict.__len__(self)

    def keys(self):
        self._force()
        return dict.keys(self)

    def values(self):
        self._force()
        return dict.values(self)

    def items(self):
        self._force()
        return dict.items(self)

    def copy(self) -> dict:
        self._force()
        return dict(dict.items(self))
