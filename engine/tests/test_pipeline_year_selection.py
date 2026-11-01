"""Which COTAHIST years the pipeline downloads, and which it re-downloads.

The ingest itself is network + Postgres and is exercised by the smoke run. What
is decided in pure code — and what a scheduled run depends on — is the year
selection: closed years are immutable and must never be re-fetched, while the
current year is rewritten by B3 every trading day, so a repeat run that reuses
the local copy re-parses yesterday's file forever with green logs and no new rows.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from b3_pipeline import level_series
from b3_pipeline import main as pipeline

AVAILABLE = [2022, 2023, 2024, 2025]
CURRENT = 2025


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    """Fake the network + parse, record (year, force) per download attempt."""
    calls: list[tuple[int, bool]] = []

    def download(year, force=False):
        calls.append((year, force))
        return tmp_path / f"COTAHIST_A{year}.ZIP"

    monkeypatch.setattr(pipeline.downloader, "detect_available_years",
                        lambda: list(AVAILABLE))
    monkeypatch.setattr(pipeline.downloader, "download_annual_file", download)
    monkeypatch.setattr(pipeline.config, "get_current_year", lambda: CURRENT)
    # Empty parse returns before the corporate-action and adjustment steps, which
    # are the network-bound half and have their own suites.
    monkeypatch.setattr(pipeline.parser, "parse_all_cotahist_files",
                        lambda _dir: pd.DataFrame())
    monkeypatch.setattr(pipeline.storage, "get_connection",
                        lambda *a, **k: sqlite3.connect(str(tmp_path / "t.sqlite")))
    return calls


def test_a_plain_run_downloads_every_available_year(downloads):
    pipeline.run_pipeline()
    assert [y for y, _ in downloads] == AVAILABLE


def test_only_the_current_year_is_re_downloaded_when_forced(downloads):
    """Forcing a closed year would re-pull gigabytes of a file that cannot have
    changed; not forcing the current one is why a scheduled run stalls."""
    pipeline.run_pipeline(force_download=True)
    assert dict(downloads) == {2022: False, 2023: False, 2024: False, 2025: True}


def test_without_the_flag_nothing_is_re_downloaded_at_all(downloads):
    pipeline.run_pipeline()
    assert not any(force for _, force in downloads)


def test_a_single_year_run_touches_only_that_year(downloads):
    pipeline.run_pipeline(year=2023)
    assert [y for y, _ in downloads] == [2023]


def test_a_year_b3_does_not_publish_stops_before_downloading_anything(downloads):
    pipeline.run_pipeline(year=1998)
    assert downloads == []


def test_start_year_keeps_that_year_and_everything_after(downloads):
    pipeline.run_pipeline(start_year=2024)
    assert [y for y, _ in downloads] == [2024, 2025]


def test_a_start_year_past_the_end_of_the_archive_downloads_nothing(downloads):
    pipeline.run_pipeline(start_year=2099)
    assert downloads == []


def test_a_b3_listing_page_that_answers_with_nothing_aborts_the_run(downloads, monkeypatch):
    """No years detected means the network is down, not that B3 has no history —
    carrying on would parse a stale data dir and report it as a successful ingest."""
    monkeypatch.setattr(pipeline.downloader, "detect_available_years", lambda: [])
    pipeline.run_pipeline()
    assert downloads == []


# --------------------------------------------------------------------------
# CLI dispatch
# --------------------------------------------------------------------------

@pytest.fixture
def dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "run_pipeline",
                        lambda **kw: seen.update({"pipeline": kw}))
    monkeypatch.setattr(pipeline, "retry_failed_companies",
                        lambda: seen.update({"retry": True}))
    monkeypatch.setattr(pipeline, "_run_update_cnpj_map",
                        lambda: seen.update({"cnpj": True}))
    return seen


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["b3_pipeline.main", *argv])
    pipeline.main()


def test_the_flags_reach_the_pipeline_under_the_names_it_expects(monkeypatch, dispatch):
    run_cli(monkeypatch, "--rebuild", "--start-year", "2010",
            "--skip-corporate-actions", "--detect-nonstandard-splits",
            "--force-download")
    assert dispatch["pipeline"] == {
        "rebuild": True, "year": None, "start_year": 2010,
        "skip_corporate_actions": True, "detect_nonstandard_splits": True,
        "force_download": True}


def test_the_default_run_asks_for_none_of_the_optional_behaviour(monkeypatch, dispatch):
    run_cli(monkeypatch)
    assert dispatch["pipeline"] == {
        "rebuild": False, "year": None, "start_year": None,
        "skip_corporate_actions": False, "detect_nonstandard_splits": False,
        "force_download": False}


@pytest.mark.parametrize("flag,expected", [
    ("--retry-failures", "retry"),
    ("--update-cnpj-map", "cnpj"),
])
def test_the_maintenance_modes_run_instead_of_the_pipeline(monkeypatch, dispatch,
                                                           flag, expected):
    run_cli(monkeypatch, flag)
    assert dispatch == {expected: True}


# --------------------------------------------------------------------------
# Steps 5c/5c2/5d/5e: third-party level series
# --------------------------------------------------------------------------

def test_level_series_steps_survive_each_others_failures(monkeypatch, caplog):
    import datetime as _dt
    import logging

    written = []
    rows = pd.DataFrame({"ticker": ["X"]})
    stored = _dt.date(2024, 5, 1)

    def boom(_today):
        raise RuntimeError("down")

    monkeypatch.setattr(level_series.index_series, "build", lambda today: rows.assign(ticker="CDI"))
    monkeypatch.setattr(level_series.ibov_series, "build", boom)
    monkeypatch.setattr(level_series.anbima_series, "ingest", boom)
    monkeypatch.setattr(level_series.tesouro_direto, "build",
                        lambda start: rows.assign(ticker=str(start)))
    monkeypatch.setattr(level_series.storage, "max_external_price_date",
                        lambda conn, source: stored if source == level_series.tesouro_direto.SOURCE else None)
    monkeypatch.setattr(level_series.storage, "upsert_external_prices",
                        lambda conn, df: written.append(df["ticker"].iloc[0]))
    with caplog.at_level(logging.INFO, logger=level_series.logger.name):
        level_series.fetch(conn=None)
    assert written == ["CDI", "2024-05-01"]
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == [
        "", "Step 5c/9: Fetching BCB index series (CDI, IPCA, Selic)...",
        "", "Step 5c2/9: Fetching Ibovespa index series...",
        "Ibovespa series fetch failed, continuing: down",
        "", "Step 5d/9: Fetching Tesouro Direto prices...",
        "", "Step 5e/9: Fetching ANBIMA fixed-income indexes...",
        "ANBIMA index fetch failed, continuing: down",
    ]


def test_level_series_skip_empty_frames_and_tesouro_starts_at_its_epoch(monkeypatch, caplog):
    import logging

    written, starts = [], []
    monkeypatch.setattr(level_series.index_series, "build", lambda today: pd.DataFrame())
    monkeypatch.setattr(level_series.ibov_series, "build", lambda today: pd.DataFrame())
    monkeypatch.setattr(level_series.anbima_series, "ingest", lambda conn: None)
    monkeypatch.setattr(level_series.tesouro_direto, "build",
                        lambda start: starts.append(start) or pd.DataFrame())
    monkeypatch.setattr(level_series.storage, "max_external_price_date", lambda conn, source: None)
    monkeypatch.setattr(level_series.storage, "upsert_external_prices",
                        lambda conn, df: written.append(df))
    with caplog.at_level(logging.ERROR, logger=level_series.logger.name):
        level_series.fetch(conn=None)
    assert written == [] and caplog.records == []
    assert starts == [level_series.tesouro_direto.TESOURO_START]
