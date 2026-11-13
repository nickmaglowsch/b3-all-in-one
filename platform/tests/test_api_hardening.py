"""Input bounds, concurrency guards and what a failure tells the tenant.

Each test pins one way a request used to end in a 500, in corrupted state, or in
a server path handed to the client:

- NaN / Infinity / negative percentages accepted, stored, then 500 on every read;
- delete-and-reinsert PUTs racing into a unique violation or a doubled set;
- a sleeve-weights cache emptied mid-request reading as "needs fundamentals";
- the public spec route paying an engine interpreter per anonymous call;
- unbounded subprocess fan-out filling the default executor;
- promote writing live sleeves without the module that owns them;
- the DARF alert firing on the due date itself, from the container's UTC clock.
"""
import asyncio
import datetime
import subprocess
import threading
import time
import types
import uuid

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _dev(client) -> dict:
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "content-type": "application/json"}


# ── Non-finite and negative inputs (M2) ───────────────────────────────────────

@pytest.mark.parametrize("method, path, raw", [
    ("PUT", "/portfolio/sleeves", '[{"strategy_ref": "CASH_CDI", "target_pct": NaN}]'),
    ("PUT", "/portfolio/sleeves",
     '[{"strategy_ref": "CASH_CDI", "target_pct": 150}, {"strategy_ref": "X", "target_pct": -50}]'),
    ("PUT", "/irpf/darf?year=2025",
     '[{"apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": NaN}]'),
    ("PUT", "/portfolio/settings/renda-fixa",
     '[{"ticker": "CDB - X", "indexer": "cdi_pct", "rate_pct": Infinity}]'),
    ("POST", "/portfolio/rebalance-plans", '{"new_cash": NaN}'),
    ("POST", "/tlh/plans", '{"sells": [{"ticker": "PETR4", "qty": Infinity}]}'),
])
async def test_non_finite_and_negative_inputs_are_a_422(
    client, monkeypatch, method, path, raw,
):
    """json.loads accepts NaN and Infinity, and every comparison with NaN is
    False — so `abs(total - 100) > 0.01` and `< 0` both waved it through, the
    row was stored, and every later read 500'd encoding it. The 422 itself has
    to survive echoing the bad input back, too."""
    from platform_app import routes

    async def gallery(session, tid):  # "X" is real: only the -50 is wrong
        return [{"name": "X"}]

    monkeypatch.setattr(routes, "_gallery", gallery)
    r = await client.request(method, path, content=raw, headers=await _dev(client))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]


async def test_lists_that_reach_argv_are_bounded(client):
    """Each of these ends up as one argv element of a subprocess; past 128 KB
    the exec fails with E2BIG, which used to be an unhandled 500."""
    h = await _dev(client)
    sells = [{"ticker": "PETR4", "qty": 1}] * 501
    assert (await client.post("/tlh/plans", json={"sells": sells}, headers=h)).status_code == 422
    spec = {"kind": "fixed_weight", "name": "Longa", "description": "x" * 2001,
            "sleeves": [{"strategy_ref": "CASH_CDI", "target_pct": 100}]}
    assert (await client.post("/strategies/user", json=spec, headers=h)).status_code == 422


# ── Delete-and-reinsert PUTs under concurrency (M8) ───────────────────────────

async def test_parallel_sleeve_replaces_never_add_up(client, monkeypatch):
    """Under READ COMMITTED the second DELETE does not see the rows the first
    just inserted, so two disjoint sets both survived: sleeves summing to 200%,
    and an advisor told to buy twice the NAV."""
    from platform_app import routes

    async def gallery(session, tid):
        return [{"name": n} for n in ("A", "B", "C", "D")]

    monkeypatch.setattr(routes, "_gallery", gallery)
    h = await auth(client, f"race-sleeves-{uuid.uuid4()}@example.com", ["mercado"])
    bodies = [[{"strategy_ref": ref, "target_pct": 100}] for ref in ("A", "B", "C", "D")]
    rs = await asyncio.gather(*(client.put("/portfolio/sleeves", json=b, headers=h)
                                for b in bodies))
    assert [r.status_code for r in rs] == [200] * 4
    got = (await client.get("/portfolio/sleeves", headers=h)).json()
    assert sum(s["target_pct"] for s in got) == 100


@pytest.mark.parametrize("path, body", [
    ("/portfolio/settings/asset-overrides", [{"ticker": "LFTB11", "treatment": "cash_cdi"}]),
    ("/portfolio/settings/renda-fixa", [{"ticker": "CDB - X", "indexer": "cdi_pct", "rate_pct": 110}]),
    ("/irpf/darf?year=2025", [{"apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": 1}]),
    ("/portfolio/settings/rebalance", {"cadence": "QE"}),
])
async def test_the_same_put_twice_at_once_is_not_a_500(client, path, body):
    """Overlapping sets trip the unique key (or, first time round, the
    rebalance_settings primary key) on whichever PUT commits second."""
    h = await auth(client, f"race-put-{uuid.uuid4()}@example.com", ["mercado", "irpf"])
    rs = await asyncio.gather(*(client.put(path, json=body, headers=h) for _ in range(4)))
    assert [r.status_code for r in rs] == [200] * 4, [r.text for r in rs]


# ── The sleeve-weights cache (M7) ─────────────────────────────────────────────

async def test_a_cache_emptied_mid_request_keeps_the_hits_it_already_had(monkeypatch):
    from platform_app import routes
    from platform_app.db import async_session_maker
    from platform_app.models import PortfolioSleeve

    tid = uuid.uuid4()
    async with async_session_maker() as s:
        s.add(PortfolioSleeve(tenant_id=tid, strategy_ref="Warm", target_pct=50))
        s.add(PortfolioSleeve(tenant_id=tid, strategy_ref="Cold", target_pct=50))
        await s.commit()
    today = datetime.date.today().isoformat()
    monkeypatch.setitem(routes._SLEEVE_WEIGHTS_CACHE, ("Warm", today, "ME"),
                        {"weights": {"WARM3": 1.0}})

    async def advise(strategy, freq, extra_env=None):
        # Another request's bounded() overflowing while this one awaits.
        routes._SLEEVE_WEIGHTS_CACHE.clear()
        return {"weights": {"COLD3": 1.0}}

    monkeypatch.setattr(routes, "_advise_strategy", advise)
    async with async_session_maker() as s:
        combined, cash, _ = await routes._combined_target(s, tid)
    assert combined == {"WARM3": 0.5, "COLD3": 0.5}


# ── The public spec route (M4) ────────────────────────────────────────────────

async def test_builtin_specs_are_answered_once_and_strangers_names_never_spawn(
    client, monkeypatch,
):
    from platform_app import enginerun, routes

    async def builtins():
        return [{"name": "Builtin"}, {"name": "CodeKind"}]

    calls: list = []

    def run_script(script, args, extra_env=None, stdin=None):
        calls.append(args[-1])
        rc = 0 if args[-1] == "Builtin" else 1
        return subprocess.CompletedProcess(args, rc, stdout='{"name": "Builtin"}', stderr="")

    monkeypatch.setattr(routes, "_builtins", builtins)
    monkeypatch.setattr(routes, "_SPEC_CACHE", {})
    monkeypatch.setattr(enginerun, "run_script", run_script)
    for _ in range(3):
        r = await client.get("/strategies/Builtin/spec")
        assert (r.status_code, r.json()) == (200, {"name": "Builtin"})
        assert (await client.get("/strategies/CodeKind/spec")).status_code == 404
        assert (await client.get(f"/strategies/{uuid.uuid4()}/spec")).status_code == 404
    assert calls == ["Builtin", "CodeKind"]


# ── Subprocess fan-out (M3) ───────────────────────────────────────────────────

async def test_offload_caps_how_many_subprocess_waits_run_at_once():
    from platform_app import subprocs

    lock, live, peak = threading.Lock(), [0], [0]

    def blocking():
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1

    await asyncio.gather(*(subprocs.offload(blocking) for _ in range(3 * subprocs.API_SLOTS)))
    assert peak[0] == subprocs.API_SLOTS


# ── Entitlement on promote (L2) ───────────────────────────────────────────────

async def test_promote_needs_the_module_that_owns_live_sleeves(client):
    from platform_app.db import async_session_maker
    from platform_app.models import UserStrategy

    h = await auth(client, f"promote-bench-{uuid.uuid4()}@example.com", ["bench"])
    tid = uuid.UUID((await client.get("/me", headers=h)).json()["id"])
    row = UserStrategy(tenant_id=tid, name="P", kind="fixed_weight",
                       spec={"sleeves": [{"asset": "CDI", "weight": 1.0}]})
    async with async_session_maker() as s:
        s.add(row)
        await s.commit()
    r = await client.post(f"/strategies/user/{row.id}/promote", headers=h)
    # The same 402 body as every other module gate, so the same paywall renders.
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["required_module"] == "mercado"


# ── DARF "atrasado" on Brasília's calendar (L5) ───────────────────────────────

@pytest.mark.parametrize("utc, status", [
    # 22:00 on the due date in Brasília is already the 31st in UTC.
    (datetime.datetime(2025, 5, 31, 1, 0, tzinfo=datetime.UTC), "pendente"),
    (datetime.datetime(2025, 5, 31, 4, 0, tzinfo=datetime.UTC), "atrasado"),
])
async def test_a_darf_is_late_only_once_its_due_date_is_over_in_brasilia(
    monkeypatch, utc, status,
):
    from platform_app import routes
    from platform_app.db import async_session_maker

    class _Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz)

    clock = types.SimpleNamespace(**{n: getattr(datetime, n) for n in dir(datetime)
                                     if not n.startswith("__")})
    clock.datetime = _Clock

    async def irpf_json(session, tid, year):
        return {"apuracao": [{"month": 4, "classe": "AÇÃO", "ir": 755.19, "gross": 25970.0,
                              "pnl": 5034.57, "compensacao": 0.0, "base": 5034.57,
                              "aliquota": 0.15}],
                "darf": [{"month": 4, "imposto": 755.19, "irrf_deduzido": 0.0,
                          "acumulado_anterior": 0.0, "darf": 755.19, "acumulado": 0.0}]}

    monkeypatch.setattr(routes, "datetime", clock)
    monkeypatch.setattr(routes, "_run_irpf_json", irpf_json)
    async with async_session_maker() as s:
        out = await routes.darf_ledger(year=2025, tenant=(None, uuid.uuid4()), session=s)
    (mes,) = out["meses"]
    assert (mes["vencimento"], mes["status"]) == ("2025-05-30", status)


# ── Stored subprocess errors (L8) ─────────────────────────────────────────────

async def test_an_import_error_goes_back_without_the_servers_paths(client):
    from platform_app.db import async_session_maker
    from platform_app.models import Import

    h = await auth(client, f"import-err-{uuid.uuid4()}@example.com")
    tid = uuid.UUID((await client.get("/me", headers=h)).json()["id"])
    async with async_session_maker() as s:
        s.add(Import(tenant_id=tid, filename="x.xlsx", stored_path="/data/uploads/x.xlsx",
                     kind="unknown", status="error",
                     error=f"ValueError: /data/uploads/{tid}/workspaces/b3import-1/"
                           "b3_imports/x.xlsx is not a B3 extract"))
        await s.commit()
    (row,) = (await client.get("/imports", headers=h)).json()
    assert row["error"] == "ValueError: x.xlsx is not a B3 extract"
