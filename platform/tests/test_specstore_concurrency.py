"""materialize_user_specs is called from both the API and the worker, which share
the artifacts volume, and from several request paths at once. It used to rmtree the
tenant's spec dir and rewrite it, which raced with itself: ~17% of calls under
3-way concurrency died with FileNotFoundError/FileExistsError, and an engine
subprocess reading the dir mid-rewrite saw a partial spec set.
"""
import json
import threading
import uuid


def test_concurrent_materialize_never_fails_or_shows_a_partial_set(tmp_path, monkeypatch):
    from platform_app import specstore

    monkeypatch.setattr(specstore.settings, "artifacts_dir", str(tmp_path))
    tenant = uuid.uuid4()
    specs = [(str(uuid.uuid4()), {"name": f"s{i}", "kind": "rank_and_hold"})
             for i in range(12)]

    failures: list[str] = []
    partial: list[int] = []

    def hammer(read_only: bool) -> None:
        for _ in range(40):
            try:
                d = specstore.materialize_user_specs(specs, tenant)
                if read_only:
                    # What the engine subprocess does: list the dir and load each
                    # spec. Never a short set, never a truncated file.
                    files = list(d.glob("*.yaml"))
                    if len(files) != len(specs):
                        partial.append(len(files))
                    for f in files:
                        json.loads(f.read_text())
            except Exception as e:  # noqa: BLE001 — the failure is the point
                failures.append(type(e).__name__)

    threads = [threading.Thread(target=hammer, args=(i % 2 == 0,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not failures, f"materialize_user_specs raced: {failures[:5]}"
    assert not partial, f"a reader saw a partial spec set: {partial[:5]}"


def test_ids_that_disappear_are_swept(tmp_path, monkeypatch):
    """Per-file writes must not turn into an append-only dir — a deleted strategy
    still has to leave the registry, or the engine keeps resolving its name."""
    from platform_app import specstore

    monkeypatch.setattr(specstore.settings, "artifacts_dir", str(tmp_path))
    tenant = uuid.uuid4()
    keep, drop = str(uuid.uuid4()), str(uuid.uuid4())

    specstore.materialize_user_specs([(keep, {"name": "a"}), (drop, {"name": "b"})], tenant)
    d = specstore.materialize_user_specs([(keep, {"name": "a"})], tenant)

    assert {f.name for f in d.glob("*.yaml")} == {f"{keep}.yaml"}


def test_specs_env_materializes_rows_or_passes_nothing(tmp_path, monkeypatch):
    """Zero-spec tenants get no env var at all — identical to a built-in call."""
    from types import SimpleNamespace

    from platform_app import specstore

    monkeypatch.setattr(specstore.settings, "artifacts_dir", str(tmp_path))
    tenant = uuid.uuid4()
    assert specstore.specs_env([], tenant) == {}

    row = SimpleNamespace(id=uuid.uuid4(), spec={"name": "x"})
    env = specstore.specs_env([row], tenant)
    d = tmp_path.resolve() / "user_specs" / str(tenant)
    assert env == {"ENGINE_USER_SPECS_DIR": str(d)}
    assert json.loads((d / f"{row.id}.yaml").read_text()) == {"name": "x"}
