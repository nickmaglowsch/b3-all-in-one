"""The sector universes (BESST Quality, TevaAtivosReais) come from CVM's company
register in the market plane, resolved to tickers by CNPJ.

A small register is written through the real pipeline functions (cad upsert,
company_tickers_pit upsert) into a throwaway SQLite plane, then read back with
data.load_sector_pit — the frame build_shared_data hands every strategy. The
plane file has no FCA archives beside it, which is the production shape: the
old loader parsed those archives, found none on the Postgres-backed worker, and
the Teva sleeve of "Teva / IVVB11 / CDI (1/3 each)" never invested.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
import yaml

import backtests.core.data as core_data
from b3_pipeline import cvm_storage, storage
from backtests.core import strategy_registry as sr
from backtests.core.config_strategy import FixedWeight
from backtests.core.data import load_sector_pit, sector_membership
from backtests.strategies.besst_quality import BesstQualityStrategy, besst_roots
from backtests.core.data import SectorRegistryEmpty
from backtests.strategies.teva_ativos_reais import TevaAtivosReaisStrategy, _ticker_sectors

SPEC = (Path(__file__).resolve().parent.parent / "backtests" / "strategies"
        / "specs" / "teva_ivvb_cdi.yaml")

# (cnpj, sector, delisting_date) as CVM's register would carry them
REGISTER = [
    ("00000000000101", "Energia Elétrica", None),                    # Eletrobras
    ("00000000000202", "Bancos", "2019-06-30"),                      # delisted bank
    ("00000000000303", "Emp. Adm. Part. - Telecomunicações", None),  # holding
    ("00000000000404", "Petróleo e Gás", None),                      # not BESST
]
# (cnpj, ticker, start, end) from FCA filings — Eletrobras renamed ELET -> AXIA
PIT_TICKERS = [
    ("00000000000101", "ELET3", "2010-01-01", "2025-11-07"),
    ("00000000000101", "AXIA3", "2025-11-10", None),
    ("00000000000202", "OLDB4", "2010-01-01", "2019-06-30"),
    ("00000000000404", "PETR4", "2010-01-01", None),
]


def _plane(tmp_path, register=REGISTER) -> str:
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    cvm_storage.upsert_cad_company_dates(conn, pd.DataFrame(
        [{"cnpj": c, "cvm_code": None, "company_name": None, "listing_date": None,
          "delisting_date": d, "sector": s} for c, s, d in register]))
    cvm_storage.upsert_company_tickers_pit(conn, pd.DataFrame(
        [{"cnpj": c, "ticker": t, "ticker_root": t[:4], "market": "BOLSA",
          "start_date": a, "end_date": b, "source": "FCA"} for c, t, a, b in PIT_TICKERS]))
    # The holding has no FCA ticker, only the B3-API root on cvm_companies —
    # which holds roots, not full tickers, in production.
    conn.execute("UPDATE cvm_companies SET ticker = 'TELH', ticker_root = 'TELH' "
                 "WHERE cnpj = '00000000000303'")
    conn.commit()
    conn.close()
    return db


def test_besst_universe_from_register(tmp_path):
    roots = besst_roots(load_sector_pit(_plane(tmp_path)))
    # rename both ways, delisted company kept, holding-prefix sector included
    assert roots == {"ELET", "AXIA", "OLDB", "TELH"}
    assert "PETR" not in roots  # non-BESST sector excluded


def test_empty_register_fails_loudly(tmp_path):
    no_sectors = [(c, None, d) for c, _, d in REGISTER]
    sp = load_sector_pit(_plane(tmp_path, no_sectors))
    assert sp.empty
    with pytest.raises(SectorRegistryEmpty, match="cvm_companies.sector"):
        besst_roots(sp)
    # the strategy raises before loading anything from the plane
    idx = pd.date_range("2020-03-31", periods=2, freq="QE")
    frame = pd.DataFrame(0.0, index=idx, columns=["ELET3"])
    shared = {"ret": frame, "close_px": frame, "fin_vol": frame, "sector_pit": sp}
    with pytest.raises(RuntimeError, match="BESST Quality"):
        BesstQualityStrategy().generate_signals(shared, {})


def test_teva_sectors_resolve_from_the_plane(tmp_path):
    # Teva's default sectors match against the same register, its roots
    # expanded to the plane's full tickers (both share classes, and the rename)
    by_ticker = _ticker_sectors(load_sector_pit(_plane(tmp_path)),
                                ["ELET3", "ELET6", "AXIA3", "PETR4", "TELH3"])
    member = sector_membership(by_ticker, ["Energia Elétrica", "Saneamento"])
    assert set(member["ticker"]) == {"ELET3", "ELET6", "AXIA3"}
    assert "TELH3" in set(by_ticker["ticker"])  # cvm_companies' root resolves too


def test_teva_blend_without_sectors_raises_instead_of_holding_cdi(monkeypatch):
    """Regression: with no sector data the Teva sleeve used to emit an all-zero
    book, FixedWeight parked its third in CDI, and the blend ran as IVVB11/CDI
    33/67 with `invested_from: null` for the sleeve. Now the run fails, naming
    the missing register."""
    idx = pd.date_range("2020-03-31", periods=4, freq="QE")
    reg = sr.StrategyRegistry()
    reg.register(TevaAtivosReaisStrategy())
    monkeypatch.setattr(sr, "get_registry", lambda: reg)
    monkeypatch.setattr(core_data, "download_benchmark",
                        lambda *a, **k: pd.Series(100.0, index=idx))
    shared = {"ret": pd.DataFrame(0.0, index=idx, columns=["AAAA3"]),
              "cdi_monthly": pd.Series(0.01, index=idx),
              "sector_pit": pd.DataFrame(columns=["root", "ref_date", "sector"])}
    with pytest.raises(RuntimeError, match="TevaAtivosReais: no CVM sector"):
        FixedWeight(yaml.safe_load(SPEC.read_text())).generate_signals(shared, {})
