"""
Tests for the current-year re-download rule that makes a repeat run meaningful.

No network: download_annual_file is stubbed, so these assert only which years
the pipeline asks to be force-downloaded.

B3 rewrites COTAHIST_A<current>.ZIP every trading day, but download_annual_file
returns the local copy when one exists. A scheduled run without force therefore
re-parses yesterday's file forever — logs look healthy, the DB never advances.
That is the exact shape of the bug that left this database seven months stale,
so it gets a test rather than a comment.
"""
from __future__ import annotations

import pandas as pd
import pytest

from b3_pipeline import config, downloader, main, parser, storage


@pytest.fixture
def capture_downloads(monkeypatch):
    """Record (year, force) per download and stop the pipeline right after."""
    calls: list[tuple[int, bool]] = []

    def fake_download(year, force=False):
        calls.append((year, force))
        return None  # no files -> parse yields nothing and run_pipeline returns

    monkeypatch.setattr(downloader, "download_annual_file", fake_download)
    monkeypatch.setattr(downloader, "detect_available_years", lambda: [2024, 2025, 2026])
    monkeypatch.setattr(config, "get_current_year", lambda: 2026)
    monkeypatch.setattr(parser, "parse_all_cotahist_files", lambda _d: pd.DataFrame())

    class _Conn:  # run_pipeline closes it in a finally
        def close(self):
            pass

    monkeypatch.setattr(storage, "get_connection", _Conn)
    monkeypatch.setattr(storage, "init_db", lambda *a, **k: None)
    return calls


def test_force_download_refreshes_only_the_current_year(capture_downloads):
    main.run_pipeline(force_download=True)

    forced = {year for year, force in capture_downloads if force}
    assert forced == {2026}, (
        "only the current year is rewritten by B3; forcing closed years "
        "re-downloads gigabytes for nothing"
    )
    assert len(capture_downloads) == 3, "every available year is still processed"


def test_without_the_flag_nothing_is_forced(capture_downloads):
    """Default stays as it was — a first-time run must not re-fetch the archive."""
    main.run_pipeline()

    assert not [year for year, force in capture_downloads if force]


def test_force_download_applies_when_targeting_the_current_year(capture_downloads):
    """The scheduled nightly path: --year <current> --force-download."""
    main.run_pipeline(year=2026, force_download=True)

    assert capture_downloads == [(2026, True)]
