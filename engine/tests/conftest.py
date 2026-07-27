"""Shared fixtures for the engine tests.

The important one is DSN isolation. `storage.get_connection` resolves the backend
from ENGINE_DATABASE_URL *before* it looks at the db_path it was handed — that
precedence is deliberate and production depends on it, because a strategy carries
a default SQLite db_path in its parameters and still has to reach the Postgres
plane.

The consequence for tests is that any module which builds a temporary SQLite
database and passes its path is silently redirected to Postgres whenever a
developer has that variable exported. 22 tests across 7 modules failed that way —
they were asserting against whatever happened to be in the real market plane
instead of the three rows they had just inserted. CI never sets the variable, so
the whole class of failure was invisible there and only appeared on a dev box
that had it in the shell.
"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "postgres: test genuinely targets the Postgres plane and needs "
        "ENGINE_DATABASE_URL left intact",
    )


@pytest.fixture(autouse=True)
def isolate_engine_dsn(request, monkeypatch):
    """Hide ENGINE_DATABASE_URL from tests that build their own SQLite fixture.

    Opt back in with @pytest.mark.postgres (module- or test-level) when the point
    of the test *is* the Postgres backend. Those modules also carry a skipif on
    storage.using_postgres(), which is evaluated at collection time — before this
    fixture runs — so the marker is what keeps the variable available to the test
    body itself.
    """
    if request.node.get_closest_marker("postgres"):
        return
    monkeypatch.delenv("ENGINE_DATABASE_URL", raising=False)


@pytest.fixture
def engine_dsn() -> str:
    """The Postgres DSN, for tests marked `postgres`."""
    dsn = os.environ.get("ENGINE_DATABASE_URL")
    if not dsn:
        pytest.skip("ENGINE_DATABASE_URL is not set")
    return dsn
