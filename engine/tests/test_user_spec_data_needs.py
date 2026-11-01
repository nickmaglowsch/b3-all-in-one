"""A user fixed_weight spec holding a code strategy that needs CVM fundamentals
(TevaAtivosReais) needs them too — directly or through a nested built-in blend.

The runners load fundamentals only when the strategy says needs_fundamentals,
and a user spec carried only what validate_spec inferred from f_* keys, which a
`strategy: TevaAtivosReais` sleeve has none of. So the run had no share counts,
Teva's book was all zeros, and the blend reported itself as IVVB11/CDI with
`invested_from: null`. The registry now inherits the flag through sleeves, and a
strategy whose fundamentals were not loaded raises instead of holding nothing.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import backtests.core.data as core_data
import backtests.strategies.teva_ativos_reais as teva
from backtests.core import strategy_registry as sr
from backtests.core.config_strategy import FixedWeight
from backtests.core.fundamentals import FundamentalsNotLoaded

BLEND = "Teva / IVVB11 / CDI (1/3 each)"
DIRECT = {"name": "user teva", "kind": "fixed_weight", "rebalance": "QE",
          "sleeves": [{"strategy": "TevaAtivosReais", "weight": 0.5},
                      {"asset": "CDI", "weight": 0.5}]}
NESTED = {"name": "user nested teva", "kind": "fixed_weight", "rebalance": "QE",
          "sleeves": [{"strategy": BLEND, "weight": 1.0}]}
IDX = pd.date_range("2016-01-31", periods=48, freq="ME")
TICKERS = ["ELET3", "CMIG4"]


@pytest.fixture
def registry(tmp_path, monkeypatch):
    # specs as the platform materializes them: JSON-as-YAML, no needs_fundamentals
    for i, spec in enumerate((DIRECT, NESTED)):
        (tmp_path / f"u{i}.yaml").write_text(json.dumps(spec))
    monkeypatch.setenv("ENGINE_USER_SPECS_DIR", str(tmp_path))
    reg = sr.StrategyRegistry()
    reg.discover()
    monkeypatch.setattr(sr, "get_registry", lambda: reg)
    return reg


def _shared(with_fundamentals: bool = True) -> dict:
    """A plane where both utilities qualify: mcap 1bn, ADTV 20M, a 6% DY."""
    full = lambda v: pd.DataFrame(v, index=IDX, columns=TICKERS)  # noqa: E731
    shared = {"ret": full(0.0), "raw_close": full(10.0), "adtv": full(2e7),
              "cdi_monthly": pd.Series(0.01, index=IDX),
              "sector_pit": pd.DataFrame({"root": ["ELET", "CMIG"],
                                          "ref_date": pd.Timestamp("1900-01-01"),
                                          "sector": "Energia Elétrica"})}
    if with_fundamentals:
        shared["f_shares_outstanding_m"] = full(1e8)
    return shared


@pytest.fixture
def plane(monkeypatch):
    monkeypatch.setattr(teva, "build_dy_12m",
                        lambda _db, tickers: {t: pd.Series(0.06, index=IDX) for t in tickers})
    monkeypatch.setattr(core_data, "download_benchmark",
                        lambda *a, **k: pd.Series(100.0, index=IDX))


def test_user_specs_inherit_needs_fundamentals(registry):
    assert registry.get("user teva").needs_fundamentals is True
    assert registry.get("user nested teva").needs_fundamentals is True


@pytest.mark.parametrize("name", ["user teva", "user nested teva"])
def test_teva_sleeve_in_a_user_spec_invests(registry, plane, name):
    _, tw = registry.get(name).generate_signals(_shared(), {})
    assert np.allclose(tw.sum(axis=1), 1.0)
    assert tw[TICKERS].iloc[-1].sum() > 0.1  # the Teva sleeve holds its names


def test_unloaded_fundamentals_raise(registry, plane):
    with pytest.raises(FundamentalsNotLoaded, match="TevaAtivosReais"):
        FixedWeight(DIRECT).generate_signals(_shared(with_fundamentals=False), {})
