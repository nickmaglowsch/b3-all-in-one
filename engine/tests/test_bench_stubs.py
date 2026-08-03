"""ENGINE_BENCH_OFFLINE — the switch every offline test and the smoke run depend on.

`_install_benchmark_stubs` monkeypatches the CDI and benchmark fetchers inside the
already-imported modules. Two ways for that to go wrong and neither is loud: it
patches one module but not the other (shared_data bound the name at import time,
so the stub silently misses and the run reaches out to BCB), or it patches when it
was not asked to and a real run quietly benchmarks against a flat line.
"""
from __future__ import annotations

import pandas as pd
import pytest

import backtests.core.data as data
import backtests.core.shared_data as sd
from scripts.run_backtest_json import _flat_series, _install_benchmark_stubs, _resolve_end

WINDOW = ("2024-01-01", "2024-03-31")


@pytest.fixture(autouse=True)
def restore_fetchers(monkeypatch):
    """The stubs are module-level assignments; leaking them would poison the rest
    of the suite with a flat CDI."""
    for mod in (data, sd):
        for name in ("download_benchmark", "download_cdi_daily"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, getattr(mod, name))


def test_a_flat_series_spans_the_window_in_business_days():
    s = _flat_series(*WINDOW, "CDI", 0.0)
    assert isinstance(s.index, pd.DatetimeIndex)
    assert s.index.min() >= pd.Timestamp(WINDOW[0])
    assert s.index.max() <= pd.Timestamp(WINDOW[1])
    assert (s.index.dayofweek < 5).all()
    assert s.pct_change().dropna().eq(0).all(), "a stub must add no signal"


def test_online_mode_leaves_the_real_fetchers_alone():
    """Otherwise a production run benchmarks against a flat line and reports
    beating the index."""
    before = (data.download_benchmark, sd.download_benchmark,
              data.download_cdi_daily, sd.download_cdi_daily)
    _install_benchmark_stubs(*WINDOW, offline=False)
    assert (data.download_benchmark, sd.download_benchmark,
            data.download_cdi_daily, sd.download_cdi_daily) == before


def test_offline_mode_patches_both_modules_not_just_the_one():
    """shared_data imported these names at import time, so patching only
    backtests.core.data leaves the call site untouched and the run hits BCB."""
    _install_benchmark_stubs(*WINDOW, offline=True)
    for fetch_bench, fetch_cdi in ((data.download_benchmark, data.download_cdi_daily),
                                   (sd.download_benchmark, sd.download_cdi_daily)):
        bench = fetch_bench("^BVSP", *WINDOW)
        cdi = fetch_cdi(*WINDOW)
        assert not bench.empty and bench.pct_change().dropna().eq(0).all()
        assert cdi.eq(0.0).all(), "stubbed CDI must accrue nothing"


def test_end_of_today_resolves_to_a_real_date():
    resolved = _resolve_end("today")
    assert pd.Timestamp(resolved).date() == pd.Timestamp.today().date()


def test_an_explicit_end_date_is_passed_through_untouched():
    assert _resolve_end("2024-03-31") == "2024-03-31"
