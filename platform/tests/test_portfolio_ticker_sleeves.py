"""Ticker sleeves in user portfolios (spec/06, spec/09): the request the MCP
server's own user asked for — {"ticker": "IVVB11.SA", "target_pct": 25} used to
fail with "sleeves.N.strategy_ref: Field required".

The engine's validate_spec.py really runs here, against a throwaway SQLite price
plane this module points `settings.engine_database_url` at — CI's b3 database
is empty, and an empty plane refuses every ticker. The plane has DIVO11 from the
start of its history, IVVB11 from 2014, and nothing else.
"""
from __future__ import annotations

import sqlite3

import pytest

from platform_app.config import settings
from platform_app.strategy_specs import PortfolioStrategyIn, _compile_portfolio
from tests.conftest import auth
from tests.test_mcp import _call, _token

pytestmark = pytest.mark.asyncio(loop_scope="session")

THIRDS = "DIVO11 / CDI / IVVB11 (thirds)"


@pytest.fixture
def plane(tmp_path, monkeypatch):
    db = tmp_path / "plane.sqlite"
    conn = sqlite3.connect(db)
    # Only the columns the save-time check reads; the engine owns the real schema.
    conn.execute("CREATE TABLE prices (ticker TEXT, date DATE, adj_close REAL)")
    conn.executemany("INSERT INTO prices VALUES (?, ?, 10.0)", [
        ("PETR4", "2005-01-03"), ("DIVO11", "2005-01-20"), ("DIVO11", "2020-01-02"),
        ("IVVB11", "2014-05-02"), ("IVVB11", "2020-01-02")])
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "engine_database_url", str(db))
    return db


def _port(name: str, *sleeves) -> dict:
    return {"kind": "fixed_weight", "name": name, "rebalance": "QE", "sleeves": list(sleeves)}


def _t(ticker, pct):
    return {"ticker": ticker, "target_pct": pct}


def _s(ref, pct):
    return {"strategy_ref": ref, "target_pct": pct}


async def test_compiles_to_the_builtin_shape():
    """Sleeve for sleeve what specs/divo_cdi_ivvb.yaml says — and the dict
    engine/tests/test_user_portfolio_parity.py runs against the built-in. Case,
    the .SA suffix and the fractional market's F are all normalised away."""
    body = PortfolioStrategyIn(**_port("Mine", _t("divo11F", 34), _s("CASH_CDI", 33),
                                       _t("IVVB11.SA", 33)))
    assert _compile_portfolio(body) == {
        "name": "Mine", "kind": "fixed_weight", "description": "", "rebalance": "QE",
        "park_in_cdi_until_live": True,
        "sleeves": [{"ticker": "DIVO11.SA", "weight": 0.34},
                    {"asset": "CDI", "weight": 0.33},
                    {"ticker": "IVVB11.SA", "weight": 0.33}]}


async def test_mcp_validates_and_saves_the_thirds_like_the_builtin(client, plane):
    h = await auth(client, "tickers-mcp@example.com", modules=["bench"])
    token = await _token(client, h)
    spec = _port("Minha DIVO/CDI/IVVB", _t("DIVO11.SA", 34), _s("CASH_CDI", 33),
                 _t("IVVB11.SA", 33))

    ok, err = await _call(client, token, "validate_strategy", spec=spec)
    assert not err, ok
    # IVVB11 lists years into the history: a warning that names the date.
    assert any("IVVB11.SA" in w and "2014-05-02" in w for w in ok["warnings"]), ok
    # Same engine spec as the built-in, but for the whole-number weights.
    builtin, _ = await _call(client, token, "get_strategy_spec", name=THIRDS)
    shape = lambda sp: (sp["rebalance"], sp.get("park_in_cdi_until_live", True),  # noqa: E731
                        [(sl.get("ticker"), sl.get("asset")) for sl in sp["sleeves"]])
    assert shape(ok["compiled_spec"]) == shape(builtin["spec"])

    made, err = await _call(client, token, "create_strategy", spec=spec)
    assert not err and made["origin"] == "mcp", made
    assert any("2014-05-02" in w for w in made["warnings"])
    rows = (await client.get("/strategies/user", headers=h)).json()
    assert rows[0]["spec"]["sleeves"][0] == {"ticker": "DIVO11.SA", "weight": 0.34}
    gallery = (await client.get("/strategies", headers=h)).json()
    assert spec["name"] in {s["name"] for s in gallery}

    # update_strategy takes the same sleeves, mixed with a strategy.
    upd, err = await _call(client, token, "update_strategy", strategy=spec["name"],
                           spec=_port(spec["name"], _s("MomSharpe", 40), _t("IVVB11", 40),
                                      _s("CASH_CDI", 20)))
    assert not err, upd
    assert upd["spec"]["sleeves"] == [{"strategy": "MomSharpe", "weight": 0.4},
                                      {"ticker": "IVVB11.SA", "weight": 0.4},
                                      {"asset": "CDI", "weight": 0.2}]


async def test_mcp_rejects_an_unknown_ticker_by_name(client, plane):
    h = await auth(client, "tickers-unknown@example.com", modules=["bench"])
    token = await _token(client, h)
    bad, err = await _call(client, token, "validate_strategy", spec=_port(
        "Typo", _t("XPTO11.SA", 50), _s("CASH_CDI", 50)))
    assert err and bad.startswith("422") and "unknown ticker 'XPTO11.SA'" in bad, bad
    assert (await client.get("/strategies/user", headers=h)).json() == []


@pytest.mark.parametrize("sleeves, needle", [
    ([_t("^BVSP", 100)], "é um índice"),
    ([_t("PETRO4", 100)], "não é um código da B3"),
    ([{"ticker": "IVVB11", "strategy_ref": "MomSharpe", "target_pct": 100}],
     "exactly one of strategy_ref / ticker"),
    ([{"target_pct": 100}], "exactly one of strategy_ref / ticker"),
    ([_t("IVVB11", 50), _t("ivvb11.sa", 50)], "IVVB11.SA is in more than one sleeve"),
])
async def test_malformed_ticker_sleeves_are_refused_before_the_engine(client, sleeves, needle):
    h = await auth(client, "tickers-shape@example.com", modules=["bench"])
    token = await _token(client, h)
    bad, err = await _call(client, token, "validate_strategy", spec=_port("Bad", *sleeves))
    assert err and bad.startswith("invalid spec") and needle in bad, bad


async def test_the_pickers_ticker_prefix_is_not_a_strategy_name(client):
    """/estrategia keys a ticker row "@ticker:<market>:<code>"; no strategy may
    be named so, or its row would share that key."""
    h = await auth(client, "tickers-reserved@example.com", modules=["bench"])
    token = await _token(client, h)
    bad, err = await _call(client, token, "validate_strategy", spec=_port(
        "@Ticker:IVVB11.SA", _s("CASH_CDI", 100)))
    assert err and "reserved" in bad, bad


async def test_a_ticker_passed_as_strategy_ref_gets_a_hint(client):
    h = await auth(client, "tickers-hint@example.com", modules=["bench"])
    r = await client.post("/strategies/user", headers=h, json=_port(
        "Hint", _s("IVVB11.SA", 100)))
    assert r.status_code == 422
    assert "goes in `ticker`" in r.json()["detail"]
