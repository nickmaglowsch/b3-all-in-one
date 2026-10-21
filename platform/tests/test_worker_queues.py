"""Which queues the worker process listens on.

The split is the whole point of running two worker services: an ingest run is
minutes to hours, and a worker that also listens on `default` would park every
user import and backtest behind a nightly refresh. Nothing else in the system
notices when that goes wrong — the jobs are enqueued, the logs are green, and
the imports just sit there. The schema guard matters for the same reason from
the other side: procrastinate's schema SQL is not idempotent, so applying it on
a database that already has the tables aborts the worker at boot.
"""
import contextlib

import pytest

from platform_app import worker


class FakeApp:
    """Stands in for the procrastinate App: records the run, fakes the DB."""

    def __init__(self, schema_exists: bool):
        self.connector = self
        self.schema_manager = self
        self._schema_exists = schema_exists
        self.applied = 0
        self.runs: list[tuple[list[str], int]] = []

    @contextlib.asynccontextmanager
    async def open_async(self):
        yield self

    async def execute_query_one_async(self, sql: str) -> dict:
        assert "procrastinate_jobs" in sql
        return {"reg": "procrastinate_jobs" if self._schema_exists else None}

    async def execute_query_all_async(self, sql: str, **_args) -> list:
        return []  # the orphan sweep; covered against a real DB in test_worker_orphans

    async def apply_schema_async(self) -> None:
        self.applied += 1

    async def run_worker_async(self, queues: list[str], concurrency: int) -> None:
        self.runs.append((queues, concurrency))


@pytest.fixture
def fake_app(monkeypatch):
    def install(schema_exists=True, queues=None):
        app = FakeApp(schema_exists)
        monkeypatch.setattr(worker, "app", app)
        if queues is None:
            monkeypatch.delenv("WORKER_QUEUES", raising=False)
        else:
            monkeypatch.setenv("WORKER_QUEUES", queues)
        return app

    return install


async def test_the_default_worker_never_listens_on_the_ingest_queue(fake_app):
    """`queues=None` would mean *every* queue, ingest included — and then a
    multi-hour COTAHIST parse holds both slots while imports queue up."""
    app = fake_app()
    await worker._run()
    assert app.runs == [(["default"], 2)]


async def test_an_empty_variable_reads_as_unset(fake_app):
    """compose passes WORKER_QUEUES through even when nothing sets it; an empty
    string must not be taken for a queue named ''."""
    app = fake_app(queues="")
    await worker._run()
    assert app.runs == [(["default"], 2)]


async def test_the_ingest_worker_runs_one_pipeline_at_a_time(fake_app):
    """Two concurrent parses on the shared VPS is how you page yourself —
    queueing_lock only stops the same kind from overlapping."""
    app = fake_app(queues="ingest")
    await worker._run()
    assert app.runs == [(["ingest"], 1)]


async def test_a_list_of_queues_is_passed_through_whole(fake_app):
    app = fake_app(queues="ingest,default")
    await worker._run()
    assert app.runs == [(["ingest", "default"], 1)]


async def test_a_fresh_database_gets_the_procrastinate_schema(fake_app):
    app = fake_app(schema_exists=False)
    await worker._run()
    assert app.applied == 1


async def test_an_existing_schema_is_left_alone(fake_app):
    """Re-applying it aborts the boot: the SQL is not idempotent."""
    app = fake_app(schema_exists=True)
    await worker._run()
    assert app.applied == 0


def test_the_entrypoint_runs_the_worker(monkeypatch):
    """`python -m platform_app.worker` is the compose command; a break here is a
    service that exits instantly and a queue nobody drains."""
    ran = []

    async def fake_run():
        ran.append(True)

    monkeypatch.setattr(worker, "_run", fake_run)
    worker.main()
    assert ran == [True]
