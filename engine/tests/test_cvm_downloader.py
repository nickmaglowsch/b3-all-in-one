"""
Tests for the CVM bulk data downloader (Task 03 — TDD).

All tests mock requests.get — no real network calls. The fetch itself lives in
downloader.download_file (covered in test_cad_downloader.py); what these pin is
that each doc type resolves to the right URL and cached path.
"""
from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from b3_pipeline import cvm_downloader


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "a;b\n")
    return buf.getvalue()


_ZIP = _zip_bytes()


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cvm_dir(tmp_path, monkeypatch):
    """Point CVM_DATA_DIR at a temporary directory for each test."""
    import b3_pipeline.config as _cfg
    monkeypatch.setattr(_cfg, "CVM_DATA_DIR", tmp_path)
    return tmp_path


# ── 1. Skip existing file ──────────────────────────────────────────────────────

def test_download_dfp_skips_existing_file(tmp_cvm_dir):
    """If the file already exists, requests.get should NOT be called."""
    # Pre-create the file
    expected = tmp_cvm_dir / "dfp_cia_aberta_2023.zip"
    expected.write_bytes(b"dummy")

    with patch("requests.get") as mock_get:
        result = cvm_downloader.download_dfp_file(2023)
        mock_get.assert_not_called()

    assert result == expected


# ── 2. Force re-download ───────────────────────────────────────────────────────

def test_download_dfp_force_redownloads(tmp_cvm_dir):
    """force=True should call requests.get even if file exists."""
    expected = tmp_cvm_dir / "dfp_cia_aberta_2023.zip"
    expected.write_bytes(b"old content")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.iter_content = MagicMock(return_value=[_ZIP])
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp) as mock_get:
        result = cvm_downloader.download_dfp_file(2023, force=True)
        mock_get.assert_called_once()

    assert result == expected
    assert expected.read_bytes() == _ZIP


# ── 3. Cleanup on failure ─────────────────────────────────────────────────────

def test_download_dfp_cleans_up_on_failure(tmp_cvm_dir):
    """On RequestException, return None and do not leave a partial file."""
    with patch("requests.get", side_effect=requests.RequestException("timeout")):
        result = cvm_downloader.download_dfp_file(2023)

    assert result is None

    partial = tmp_cvm_dir / "dfp_cia_aberta_2023.zip"
    assert not partial.exists(), "Partial file should be cleaned up after failure"


# ── 4. Each doc type keeps its own base URL and filename ──────────────────────

@pytest.mark.parametrize("fn, stem, base_attr", [
    (cvm_downloader.download_dfp_file, "dfp", "CVM_DFP_BASE_URL"),
    (cvm_downloader.download_itr_file, "itr", "CVM_ITR_BASE_URL"),
    (cvm_downloader.download_fre_file, "fre", "CVM_FRE_BASE_URL"),
    (cvm_downloader.download_fca_file, "fca", "CVM_FCA_BASE_URL"),
])
def test_doc_type_resolves_to_its_own_url_and_path(tmp_cvm_dir, fn, stem, base_attr):
    """Sharing one fetch must not collapse four datasets onto one URL."""
    import b3_pipeline.config as _cfg
    from b3_pipeline import downloader

    with patch.object(downloader, "download_file") as mock_dl:
        fn(2023)

    url, path = mock_dl.call_args.args
    assert url == f"{getattr(_cfg, base_attr)}{stem}_cia_aberta_2023.zip"
    assert path == tmp_cvm_dir / f"{stem}_cia_aberta_2023.zip"


# ── 5. A failed refresh keeps the cached copy ─────────────────────────────────

def _cached(tmp_cvm_dir):
    path = tmp_cvm_dir / "fre_cia_aberta_2023.zip"
    path.write_bytes(_ZIP)
    return path


def test_failed_refresh_keeps_the_cached_file(tmp_cvm_dir):
    """CVM outage on a forced re-download: old ZIP intact, no .part left."""
    path = _cached(tmp_cvm_dir)
    with patch("requests.get", side_effect=requests.ConnectionError("refused")):
        assert cvm_downloader.download_fre_file(2023, force=True) is None
    assert path.read_bytes() == _ZIP
    assert list(tmp_cvm_dir.iterdir()) == [path]


def test_non_zip_payload_does_not_overwrite(tmp_cvm_dir):
    """A 200 maintenance page is not a ZIP: rejected, cached copy kept."""
    path = _cached(tmp_cvm_dir)
    resp = MagicMock(headers={}, iter_content=MagicMock(return_value=[b"<html>manutencao</html>"]))
    with patch("requests.get", return_value=resp):
        assert cvm_downloader.download_fre_file(2023, force=True) is None
    assert path.read_bytes() == _ZIP
    assert list(tmp_cvm_dir.iterdir()) == [path]
