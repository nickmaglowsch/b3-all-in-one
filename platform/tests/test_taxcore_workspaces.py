"""What each taxcore run writes into its throwaway workspace.

The IRPF report, the DARF apuração, the TLH plan and the import task each build
a workspace, fill it with CSVs taxcore reads byte-for-byte, and shell one module.
The subprocess is faked here so the test sees exactly what landed on disk and
which argv was run — the contract the shared helpers must keep.
"""
import datetime
import subprocess
import uuid
from pathlib import Path

import pytest

from platform_app import b3run, routes, tasks
from platform_app.db import async_session_maker
from platform_app.models import CashEvent, CostBasisOverride, Import, Position, Trade

pytestmark = pytest.mark.asyncio(loop_scope="session")

TRADES = (
    "date,side,market,maturity,broker,ticker,qty,price,amount,__source_file,signed_qty\n"
    "2025-03-10,C,Mercado à Vista,,XP,PETR4,100.0,30.5,3050.0,neg.xlsx,100.0\n"
)
EVENTS = (
    "date,ticker,event_type,event_category,is_amortization,qty,price,amount,broker,"
    "__source_file\n"
    "2025-04-01,PETR4,Dividendo,dividend,False,100.0,0.5,50.0,XP,mov.xlsx\n"
)
OVERRIDES = (
    "date,ticker,kind,qty,price,amount,note\n"
    "2024-01-02,PETR4,buy,10.0,20.0,200.0,herança\n"
)
POSITIONS = "ticker,qty,avg_cost,total_cost,market\nPETR4,100.0,30.5,3050.0,acoes\n"


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Replace b3run.run_module; record argv + every non-script file in the ws."""
    monkeypatch.setattr(b3run.settings, "uploads_dir", str(tmp_path))
    seen: dict = {}

    def fake(ws: Path, args: list[str]) -> subprocess.CompletedProcess:
        seen["args"] = args
        seen["files"] = {
            str(p.relative_to(ws)): p.read_text()
            for p in sorted(ws.rglob("*"))
            if p.is_file() and p.parts[len(ws.parts)] != "scripts"
            and p.name != "sitecustomize.py"
        }
        # load_b3 "fails" so the import task stops before reading CSVs back.
        rc = 1 if args[0] == "scripts.load_b3" else 0
        return subprocess.CompletedProcess(args, rc, stdout='{"apuracao": []}', stderr="x")

    monkeypatch.setattr(b3run, "run_module", fake)
    monkeypatch.setattr(routes, "_latest_engine_prices", _fake_prices)
    return seen


async def _fake_prices(tickers):
    return [[t, 31.0] for t in sorted(set(tickers))]


async def _seed(tmp_path: Path) -> uuid.UUID:
    tid = uuid.uuid4()
    extract = tmp_path / "mov-src.xlsx"
    extract.write_text("movimentacao bytes")
    async with async_session_maker() as s:
        s.add(Trade(tenant_id=tid, date=datetime.date(2025, 3, 10), side="C",
                    market="Mercado à Vista", maturity=None, broker="XP", ticker="PETR4",
                    qty=100.0, price=30.5, amount=3050.0, source_file="neg.xlsx",
                    signed_qty=100.0))
        s.add(CashEvent(tenant_id=tid, date=datetime.date(2025, 4, 1), ticker="PETR4",
                        event_type="Dividendo", event_category="dividend",
                        is_amortization=False, qty=100.0, price=0.5, amount=50.0,
                        broker="XP", source_file="mov.xlsx"))
        s.add(Position(tenant_id=tid, ticker="PETR4", qty=100.0, avg_cost=30.5,
                       total_cost=3050.0, market="acoes"))
        s.add(CostBasisOverride(tenant_id=tid, date=datetime.date(2024, 1, 2),
                                ticker="PETR4", kind="buy", qty=10.0, price=20.0,
                                amount=200.0, note="herança"))
        s.add(Import(tenant_id=tid, filename="movimentacao-2025.xlsx",
                     stored_path=str(extract), kind="movimentacao"))
        await s.commit()
    return tid


async def test_irpf_report_workspace(captured, tmp_path):
    tid = await _seed(tmp_path)
    async with async_session_maker() as s:
        out = await routes.irpf_report(routes.IrpfRequest(year=2025), (None, tid), s)
    assert out == {"year": 2025, "markdown": '{"apuracao": []}'}
    assert captured["args"] == ["scripts.irpf_2026", "--year", "2025"]
    assert captured["files"] == {
        "b3_imports/movimentacao-2025.xlsx": "movimentacao bytes",
        "data/cash_events.csv": EVENTS,
        "data/cost_basis_overrides.csv": OVERRIDES,
        "data/trades.csv": TRADES,
    }
    assert not any(b3run.workspace_root(tid).iterdir()), "workspace left behind"


async def test_darf_apuracao_workspace(captured, tmp_path):
    tid = await _seed(tmp_path)
    async with async_session_maker() as s:
        assert await routes._run_apuracao(s, tid, 2025) == []
    assert captured["args"] == ["scripts.irpf_2026", "--year", "2025", "--json"]
    assert captured["files"] == {
        "data/cash_events.csv": EVENTS,
        "data/trades.csv": TRADES,
    }


async def test_tlh_plan_workspace(captured, tmp_path):
    tid = await _seed(tmp_path)
    async with async_session_maker() as s:
        assert await routes._run_tlh_plan(
            s, tid, [("PETR4", 10)], exclude=["VALE3"]) == {"apuracao": []}
    assert captured["args"] == [
        "scripts.tlh_plan", "--json", "--sells", "PETR4:10", "--exclude", "VALE3"]
    today = datetime.date.today().isoformat()
    assert captured["files"] == {
        "data/cash_events.csv": EVENTS,
        f"data/external_prices/{today}.csv": "papel,cotacao\nPETR4,31.0\n",
        "data/positions.csv": POSITIONS,
        "data/trades.csv": TRADES,
    }


async def test_import_task_workspace(captured, tmp_path):
    tid = await _seed(tmp_path)
    out = await tasks.process_imports.func(str(tid))
    assert out == {"tenant_id": str(tid), "status": "error", "error": "x"}
    assert captured["args"] == ["scripts.load_b3"]
    assert captured["files"] == {
        "b3_imports/movimentacao-2025.xlsx": "movimentacao bytes",
        "data/cost_basis_overrides.csv": OVERRIDES,
    }
    assert not any(b3run.workspace_root(tid).iterdir()), "workspace left behind"
