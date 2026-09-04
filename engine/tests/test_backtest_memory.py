"""What keeps a backtest inside the container, and what it says when it cannot.

A US window of 2000..today is ~11,000 symbols over ~6,500 sessions. Every daily
(date x ticker) panel that touches is ~580 MB, and the builder used to make a
dozen of them for every run whatever the strategy read — which is how a US
backtest reached the worker's mem_limit and came back to the tenant as
`run_backtest_json.py was killed by SIGKILL` with nothing else in it.

Nothing here measures megabytes: peak RSS is the machine's business, not a test's,
and asserting on it would be flaky on every runner. What is tested is the
mechanism — that an unread panel is never built, that a built one is built once,
that building it lazily gives the same numbers as building it eagerly, and that
the process now has a budget it fails legibly against.
"""
from __future__ import annotations

import resource
import sqlite3
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import storage
from backtests.core import memory, mean_rev_helpers
from backtests.core.shared_data import build_shared_data


# ── LazyShared: the mapping the bundle is ─────────────────────────────────────

def test_a_deferred_frame_is_not_built_until_it_is_read():
    built = []
    bundle = memory.LazyShared({"ret": 1})
    bundle.defer("panel", lambda: built.append(1) or "frame")

    assert built == []
    assert bundle["panel"] == "frame"
    assert built == [1]


def test_a_deferred_frame_is_built_once():
    built = []
    bundle = memory.LazyShared()
    bundle.defer("panel", lambda: built.append(1) or object())

    assert bundle["panel"] is bundle["panel"]
    assert built == [1]


def test_membership_and_get_see_a_deferred_key_without_building_it():
    built = []
    bundle = memory.LazyShared()
    bundle.defer("panel", lambda: built.append(1) or "frame")

    assert "panel" in bundle
    assert built == [], "`in` must not build the frame"
    assert bundle.get("panel") == "frame"
    assert bundle.get("nope", "dflt") == "dflt"


def test_a_missing_key_still_raises_keyerror():
    with pytest.raises(KeyError):
        memory.LazyShared()["nope"]


def test_copying_the_bundle_flat_still_gets_every_frame():
    """`dict(bundle)` and `{**bundle}` take CPython's dict-merge fast path, which
    copies a subclass's table directly and skips `keys()` — unless the subclass
    overrides `__iter__`, which LazyShared does for exactly this reason. Without
    it every untouched panel would silently vanish from the copy."""
    bundle = memory.LazyShared({"ret": 1})
    bundle.defer("panel", lambda: "frame")

    assert dict(bundle) == {"ret": 1, "panel": "frame"}

    other = memory.LazyShared({"ret": 1})
    other.defer("panel", lambda: "frame")
    assert {**other, "extra": 2} == {"ret": 1, "panel": "frame", "extra": 2}
    assert sorted(other.keys()) == ["panel", "ret"]
    assert len(other) == 2


def test_an_alias_is_the_same_object_not_a_copy():
    frame = object()
    bundle = memory.LazyShared({"ibov_px": frame})
    bundle.alias("bench_px", "ibov_px")
    assert bundle["bench_px"] is frame


def test_a_group_is_built_once_however_many_of_its_keys_are_read():
    """The two high/low panels come off one read of the plane. Registering them
    as independent thunks would run that read twice."""
    reads = []

    def _build():
        reads.append(1)
        return {"high": "H", "low": "L"}

    bundle = memory.LazyShared()
    bundle.defer_group(("high", "low"), _build)
    assert bundle["low"] == "L"
    assert bundle["high"] == "H"
    assert reads == [1]
    assert bundle.deferred_keys() == set()


def test_forcing_a_group_terminates():
    """A grouped thunk installs its siblings' values directly, so reading them
    back through `__missing__` would never retire their thunks. `_force` pops."""
    bundle = memory.LazyShared()
    bundle.defer_group(("a", "b"), lambda: {"a": 1, "b": 2})
    assert dict(bundle) == {"a": 1, "b": 2}


# ── the budget ────────────────────────────────────────────────────────────────

def test_the_budget_comes_off_the_env_when_it_is_set(monkeypatch):
    monkeypatch.setenv("ENGINE_BACKTEST_MEM_MB", "1234")
    assert memory.budget_mb() == 1234


def test_a_zero_budget_means_no_guard_at_all(monkeypatch):
    """The opt-out. A host whose allocator makes the rlimit fire early needs a
    way out that is not a code change."""
    monkeypatch.setenv("ENGINE_BACKTEST_MEM_MB", "0")
    assert memory.budget_mb() is None
    assert memory.guard() is None


def test_the_budget_is_a_fraction_of_the_container_ceiling(monkeypatch):
    monkeypatch.delenv("ENGINE_BACKTEST_MEM_MB", raising=False)
    monkeypatch.delenv("ENGINE_BACKTEST_MEM_FRACTION", raising=False)
    with patch.object(memory, "container_limit_mb", return_value=4096):
        assert memory.budget_mb() == int(4096 * 0.8)


def test_no_container_ceiling_means_no_budget(monkeypatch):
    """An unconstrained host must run exactly as it did before."""
    monkeypatch.delenv("ENGINE_BACKTEST_MEM_MB", raising=False)
    with patch.object(memory, "container_limit_mb", return_value=None):
        assert memory.budget_mb() is None


def test_the_no_limit_sentinel_is_not_read_as_a_ceiling(tmp_path, monkeypatch):
    """cgroup v2 writes the literal "max"; v1 writes a number near 2**63. Reading
    either as a ceiling would install a nonsense rlimit."""
    v2, v1 = tmp_path / "memory.max", tmp_path / "limit_in_bytes"
    v2.write_text("max\n")
    monkeypatch.setattr(memory, "_CGROUP_LIMIT_FILES", (v2, v1))
    assert memory.container_limit_mb() is None

    v2.write_text(str(2 ** 63 - 1))
    assert memory.container_limit_mb() is None

    v2.write_text(str(4 * 1024 * 1024 * 1024))
    assert memory.container_limit_mb() == 4096


def test_a_tiny_budget_is_refused_rather_than_installed(monkeypatch):
    """Below the floor the process dies during imports, which reads as "pandas is
    broken" rather than "the ceiling is too low"."""
    monkeypatch.delenv("ENGINE_BACKTEST_MEM_MB", raising=False)
    with patch.object(memory, "container_limit_mb", return_value=100):
        assert memory.budget_mb() is None


def test_the_guard_only_ever_lowers(monkeypatch):
    """A caller that already runs under a tighter rlimit keeps it."""
    with patch.object(resource, "getrlimit", return_value=(512 * 1024 * 1024,
                                                           512 * 1024 * 1024)):
        with patch.object(resource, "setrlimit") as setter:
            assert memory.guard(4096) == 512
            setter.assert_not_called()


def test_the_guard_installs_the_budget_it_was_given():
    installed = []
    with patch.object(resource, "getrlimit",
                      return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY)):
        with patch.object(resource, "setrlimit",
                          side_effect=lambda w, v: installed.append(v)):
            assert memory.guard(2048) == 2048
    assert installed == [(2048 * 1024 * 1024, resource.RLIM_INFINITY)]


def test_peak_rss_is_a_positive_number_of_megabytes():
    assert 0 < memory.peak_rss_mb() < 1_000_000


def test_the_trace_is_silent_unless_asked_for(capsys, monkeypatch):
    monkeypatch.delenv("ENGINE_MEM_TRACE", raising=False)
    memory.trace("stage")
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("ENGINE_MEM_TRACE", "1")
    memory.trace("stage")
    err = capsys.readouterr()
    assert "stage" in err.err
    assert err.out == "", "stdout is a JSON payload; a stray line there fails a run"


# ── the bundle, on a real (tiny) plane ────────────────────────────────────────

DATES = pd.bdate_range("2019-06-03", "2020-03-31")


@pytest.fixture
def plane(tmp_path) -> str:
    """A two-name B3 plane long enough for a 200-day rolling window to exist."""
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(conn)
    rng = np.random.default_rng(7)
    for ticker in ("PETR4", "VALE3"):
        px = 20.0
        for day in DATES:
            px *= float(1 + rng.normal(0, 0.01))
            conn.execute(
                "INSERT INTO prices (ticker, isin_code, date, open, high, low,"
                " close, adj_close, split_adj_high, split_adj_low,"
                " split_adj_close, volume) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, "UNKNOWN", day.strftime("%Y-%m-%d"), px, px * 1.01,
                 px * 0.99, px, px, px * 1.01, px * 0.99, px, 5_000_000.0))
    conn.commit()
    conn.close()
    return db


def _mock_cdi(start, end):
    idx = pd.bdate_range(start, end)
    return pd.Series(0.0002, index=idx, name="CDI")


def _mock_bench(ticker, start, end, **kwargs):
    idx = pd.bdate_range(start, end)
    return pd.Series(100.0, index=idx, name=ticker)


@pytest.fixture
def bundle(plane):
    with patch("backtests.core.shared_data.download_cdi_daily", _mock_cdi), \
         patch("backtests.core.shared_data.download_benchmark", _mock_bench):
        yield build_shared_data(plane, "2019-06-01", "2020-03-31", "ME")


DEFERRED = {"ma200_m", "above_ma200", "dist_ma200", "atr_m", "atr_20d_daily",
            "autocorr_20d", "autocorr_60d", "high_low_range_20d",
            "rolling_vol_20d_daily", "rolling_vol_60d_daily", "tradable",
            "split_adj_high", "split_adj_low"}


def test_the_daily_panels_arrive_unbuilt(bundle):
    """The headline. None of these is built by a run that does not read it."""
    assert DEFERRED <= bundle.deferred_keys()


def test_the_monthly_frames_are_there_eagerly(bundle):
    """Almost every strategy reads these and they are one row per rebalance —
    deferring them would buy nothing and cost a thunk per lookup."""
    for key in ("ret", "px", "adtv", "raw_close", "has_glitch", "cdi_monthly",
                "ibov_ret", "mf_composite", "delist_ret", "market"):
        assert dict.__contains__(bundle, key), key


def test_a_daily_panel_is_read_once_and_only_when_named(bundle):
    """A panel is a query, so the bundle has to be able to say which one. The
    high-low range names three; nothing reads the rest, and a second read of one
    already built is a dict lookup."""
    import backtests.core.panels as plane

    with patch.object(plane, "stream_market_panels",
                      wraps=plane.stream_market_panels) as reader:
        bundle["high_low_range_20d"]
        bundle["split_adj_high"]
        bundle["split_adj_low"]
        bundle["split_adj_close"]
    asked = [call.kwargs["panels"] for call in reader.call_args_list]
    assert asked == [("split_adj_high",), ("split_adj_low",),
                     ("split_adj_close",)], "one read per panel, in the order named"
    assert "adj_close" in bundle.deferred_keys(), "and nothing else was touched"


def test_batching_the_universe_does_not_change_the_bundle(plane, monkeypatch):
    """The whole architecture in one assertion. The grid is reduced a batch at a
    time so the market's daily bars are never resident; if that produced anything
    but the frames a whole-market load produced, every backtest would quietly
    move."""
    import backtests.core.panels as plane_mod

    def _build():
        with patch("backtests.core.shared_data.download_cdi_daily", _mock_cdi), \
             patch("backtests.core.shared_data.download_benchmark", _mock_bench):
            return build_shared_data(plane, "2019-06-01", "2020-03-31", "ME")

    monkeypatch.setattr(plane_mod, "SYMBOL_BATCH", 1)      # one name per batch
    streamed = _build()
    monkeypatch.setattr(plane_mod, "SYMBOL_BATCH", 500)    # the whole market
    whole = _build()

    for key in ("px", "ret", "raw_close", "adtv", "log_ret", "has_glitch",
                "mf_composite", "vol_5m", "vol_2m", "adj_close",
                "split_adj_close", "close_px", "fin_vol", "atr_m", "ma200_m",
                "autocorr_20d", "rolling_vol_20d_daily", "high_low_range_20d"):
        pd.testing.assert_frame_equal(streamed[key], whole[key], obj=key)


def test_the_grid_is_reduced_without_the_market_ever_being_resident(plane):
    """What `build_shared_data` keeps is the rebalance grid. A daily panel is a
    query it has not made — which is the difference between ~26 MB a frame and
    ~580 MB a frame on the US plane."""
    with patch("backtests.core.shared_data.download_cdi_daily", _mock_cdi), \
         patch("backtests.core.shared_data.download_benchmark", _mock_bench):
        shared = build_shared_data(plane, "2019-06-01", "2020-03-31", "ME")

    for key in ("adj_close", "split_adj_close", "close_px", "fin_vol",
                "split_adj_high", "split_adj_low"):
        assert not dict.__contains__(shared, key), f"{key} should not be resident"
        assert key in shared, f"{key} should still be readable"
    assert shared["px"].shape[0] < shared["adj_close"].shape[0], \
        "the grid is coarser than the bars it came from"


def test_a_panel_can_be_read_for_a_subset_of_names(plane):
    with patch("backtests.core.shared_data.download_cdi_daily", _mock_cdi), \
         patch("backtests.core.shared_data.download_benchmark", _mock_bench):
        shared = build_shared_data(plane, "2019-06-01", "2020-03-31", "ME")

    one = shared["panel"]("adj_close", columns=["VALE3"])
    assert list(one.columns) == ["VALE3"]
    pd.testing.assert_series_equal(one["VALE3"], shared["adj_close"]["VALE3"])
    assert one.index.equals(shared["adj_close"].index), "same sessions either way"


def test_a_name_the_plane_has_no_bars_for_comes_back_empty_not_misaligned(plane):
    """Columns are placed by name. A requested symbol the plane never printed is
    absent from its batch, and placing by position would shift every column
    after it one to the left."""
    with patch("backtests.core.shared_data.download_cdi_daily", _mock_cdi), \
         patch("backtests.core.shared_data.download_benchmark", _mock_bench):
        shared = build_shared_data(plane, "2019-06-01", "2020-03-31", "ME")

    got = shared["panel"]("adj_close", columns=["PETR4", "NOPE3", "VALE3"])
    assert list(got.columns) == ["PETR4", "NOPE3", "VALE3"]
    assert got["NOPE3"].isna().all()
    pd.testing.assert_series_equal(got["VALE3"], shared["adj_close"]["VALE3"])


def test_daily_returns_are_the_panel_diffed_without_the_panel(plane):
    """`pct_change` is per ticker, so it belongs in the batch — reading the
    prices and then diffing them holds both at once."""
    with patch("backtests.core.shared_data.download_cdi_daily", _mock_cdi), \
         patch("backtests.core.shared_data.download_benchmark", _mock_bench):
        shared = build_shared_data(plane, "2019-06-01", "2020-03-31", "ME")

    pd.testing.assert_frame_equal(
        shared["daily_returns"]("split_adj_close"),
        shared["split_adj_close"].pct_change())


def test_releasing_the_heap_is_safe_to_call_and_says_nothing():
    """Best effort by contract: a platform without glibc keeps what it had."""
    assert memory.release() is None
    assert memory.release() is None


def test_the_daily_nav_path_reads_only_the_names_the_book_held(bundle):
    """`strategy_daily_values` wants a dozen columns. Handing it the market's
    whole daily close to use twenty of them is what did not fit."""
    from backtests.core.metrics import strategy_daily_values
    import backtests.core.panels as plane

    # A column per candidate, a zero in all but one — the shape a real
    # target_weights frame has, and the reason "scope to target_weights.columns"
    # scoped nothing.
    weights = pd.DataFrame({"PETR4": [1.0, 1.0], "VALE3": [0.0, 0.0]},
                           index=bundle["ret"].index[-2:])
    with patch.object(plane, "stream_market_panels",
                      wraps=plane.stream_market_panels) as reader:
        out = strategy_daily_values(bundle, weights, 100_000.0)
    assert not out.empty
    assert reader.call_args.kwargs["symbols"] == ["PETR4"], \
        "a name the book never held is not a name to read bars for"


def test_a_deferred_panel_is_what_the_eager_one_used_to_be(bundle, plane):
    """Laziness is a memory change, not a numbers change. Every deferred feature
    must equal what `compute_mean_rev_features` builds from the same panels."""
    eager = mean_rev_helpers.compute_mean_rev_features(
        bundle["adj_close"], bundle["split_adj_high"], bundle["split_adj_low"],
        bundle["split_adj_close"], bundle["cdi_daily"],
        bundle["ibov_px"].pct_change(), bundle["ibov_px"])
    for key, expected in eager.items():
        got = bundle[key]
        if isinstance(expected, pd.DataFrame):
            pd.testing.assert_frame_equal(got, expected)
        else:
            pd.testing.assert_series_equal(got, expected)


def test_the_ma200_flags_agree_with_the_daily_rolling_mean(bundle):
    ma200_m = (bundle["adj_close"].rolling(200, min_periods=200).mean()
               .resample("ME").last())
    pd.testing.assert_frame_equal(bundle["ma200_m"], ma200_m)
    pd.testing.assert_frame_equal(bundle["above_ma200"], bundle["px"] > ma200_m)
    pd.testing.assert_frame_equal(bundle["dist_ma200"], bundle["px"] / ma200_m - 1)


def test_the_atr_frames_agree_with_the_ewm_proxy(bundle):
    daily = bundle["adj_close"].pct_change().abs().ewm(span=14, min_periods=14).mean()
    pd.testing.assert_frame_equal(bundle["atr_20d_daily"], daily)
    pd.testing.assert_frame_equal(bundle["atr_m"], daily.resample("ME").last())


def test_a_signal_expression_reads_a_deferred_frame_without_flattening_the_bundle():
    """`_eval_expr` used to `dict(shared)`, which would build every panel so that
    an expression naming one could read it."""
    from backtests.core import config_strategy

    frames = pd.DataFrame({"A": [1.0, 2.0, 3.0]})
    bundle = memory.LazyShared({"ret": frames})
    built = []
    bundle.defer("atr_m", lambda: built.append("atr") or frames)
    bundle.defer("vol_20d", lambda: built.append("vol") or frames)

    out = config_strategy.resolve_factor({"expr": "-atr_m"}, bundle)
    pd.testing.assert_frame_equal(out, -frames)
    assert built == ["atr"], "only the frame the expression names"


def test_a_scalar_param_still_beats_a_frame_of_the_same_name():
    """The ChainMap has to keep the precedence `dict(shared)` + `update(scalars)`
    had, or a `lookback` slider stops reaching the expression."""
    from backtests.core import config_strategy

    bundle = memory.LazyShared({"ret": pd.DataFrame({"A": [1.0]}),
                                "lookback": pd.DataFrame({"A": [99.0]})})
    out = config_strategy.resolve_factor(
        {"expr": "ret * lookback"}, bundle, {"lookback": 3})
    pd.testing.assert_frame_equal(out, pd.DataFrame({"A": [3.0]}))
