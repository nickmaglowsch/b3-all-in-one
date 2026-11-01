"""The one streaming downloader, exercised through its CVM callers.

`downloader.download_file` is the single implementation of "stream a URL to a
path, skip it if it's already there, clean up the partial on failure". CAD, IPE,
DFP/ITR/FRE/FCA and COTAHIST all route through it, so `requests.get` is patched
where it actually lives — in `b3_pipeline.downloader` — not in each caller.
"""
from __future__ import annotations

import io
import zipfile
from unittest.mock import patch, MagicMock

import requests


def _ok_response(body: bytes = b"hello data") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.headers = {"content-length": str(len(body))}
    resp.iter_content.return_value = [body]
    return resp


def test_download_cad_file_skips_when_exists(tmp_path):
    """If file already exists (and force=False), requests.get is never called."""
    from b3_pipeline import cad_downloader, config

    dummy_file = tmp_path / config.CVM_CAD_FILENAME
    dummy_file.write_bytes(b"dummy content")

    with patch("b3_pipeline.cad_downloader.config") as mock_cfg, \
         patch("b3_pipeline.downloader.requests.get") as mock_get:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_CAD_BASE_URL = "https://example.com/"
        mock_cfg.CVM_CAD_FILENAME = config.CVM_CAD_FILENAME

        result = cad_downloader.download_cad_file(force=False)
        mock_get.assert_not_called()
        assert result == dummy_file


def test_download_cad_file_downloads_when_missing(tmp_path):
    """When file does not exist, requests.get is called once and bytes land."""
    from b3_pipeline import cad_downloader, config

    with patch("b3_pipeline.cad_downloader.config") as mock_cfg, \
         patch("b3_pipeline.downloader.requests.get") as mock_get:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_CAD_BASE_URL = "https://example.com/"
        mock_cfg.CVM_CAD_FILENAME = config.CVM_CAD_FILENAME
        mock_get.return_value = _ok_response()

        result = cad_downloader.download_cad_file(force=False)
        mock_get.assert_called_once()
        assert result is not None
        assert result.read_bytes() == b"hello data"


def test_download_cad_file_force_redownloads(tmp_path):
    """When force=True, existing file is re-downloaded."""
    from b3_pipeline import cad_downloader, config

    dummy_file = tmp_path / config.CVM_CAD_FILENAME
    dummy_file.write_bytes(b"old content")

    with patch("b3_pipeline.cad_downloader.config") as mock_cfg, \
         patch("b3_pipeline.downloader.requests.get") as mock_get:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_CAD_BASE_URL = "https://example.com/"
        mock_cfg.CVM_CAD_FILENAME = config.CVM_CAD_FILENAME
        mock_get.return_value = _ok_response(b"new data")

        result = cad_downloader.download_cad_file(force=True)
        mock_get.assert_called_once()
        assert result.read_bytes() == b"new data"


def test_failed_download_leaves_no_partial_file(tmp_path):
    """A mid-stream failure returns None AND removes the truncated file — a
    partial ZIP left on disk would be skipped as "already downloaded" forever."""
    from b3_pipeline import cad_downloader, config

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.headers = {"content-length": "999"}
    resp.iter_content.side_effect = requests.ConnectionError("dropped")

    with patch("b3_pipeline.cad_downloader.config") as mock_cfg, \
         patch("b3_pipeline.downloader.requests.get", return_value=resp):
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_CAD_BASE_URL = "https://example.com/"
        mock_cfg.CVM_CAD_FILENAME = config.CVM_CAD_FILENAME

        assert cad_downloader.download_cad_file() is None
        assert not (tmp_path / config.CVM_CAD_FILENAME).exists()


def test_ipe_download_uses_the_same_downloader(tmp_path):
    """IPE is the same fetch with a per-year filename, not its own copy of it."""
    from b3_pipeline import ipe_downloader

    with patch("b3_pipeline.ipe_downloader.config") as mock_cfg, \
         patch("b3_pipeline.downloader.requests.get") as mock_get:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_IPE_BASE_URL = "https://example.com/"
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w") as zf:
            zf.writestr("ipe.csv", "a")
        mock_get.return_value = _ok_response(body.getvalue())

        result = ipe_downloader.download_ipe_file(2024)
        assert result == tmp_path / "ipe_cia_aberta_2024.zip"
        assert result.read_bytes() == body.getvalue()
        assert mock_get.call_args.args[0] == "https://example.com/ipe_cia_aberta_2024.zip"

        # Second call with the file now on disk short-circuits.
        mock_get.reset_mock()
        assert ipe_downloader.download_ipe_file(2024) == result
        mock_get.assert_not_called()


# ── One implementation, not four ──────────────────────────────────────────────
# The behavioural tests above pass against copy-pasted downloaders too: patching
# `b3_pipeline.downloader.requests.get` sets an attribute on the shared `requests`
# module, so it intercepts every copy at once. These pin the structure directly —
# each caller owns its URL and path and nothing else.

def test_cad_delegates_to_the_shared_downloader(tmp_path):
    from b3_pipeline import cad_downloader, config, downloader

    with patch("b3_pipeline.cad_downloader.config") as mock_cfg, \
         patch.object(downloader, "download_file") as mock_dl:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_CAD_BASE_URL = "https://example.com/"
        mock_cfg.CVM_CAD_FILENAME = config.CVM_CAD_FILENAME

        cad_downloader.download_cad_file(force=True)
        mock_dl.assert_called_once_with(
            f"https://example.com/{config.CVM_CAD_FILENAME}",
            tmp_path / config.CVM_CAD_FILENAME,
            force=True,
        )


def test_ipe_delegates_to_the_shared_downloader(tmp_path):
    from b3_pipeline import downloader, ipe_downloader

    with patch("b3_pipeline.ipe_downloader.config") as mock_cfg, \
         patch.object(downloader, "download_file") as mock_dl:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_IPE_BASE_URL = "https://example.com/"

        ipe_downloader.download_ipe_file(2024, force=True)
        mock_dl.assert_called_once_with(
            "https://example.com/ipe_cia_aberta_2024.zip",
            tmp_path / "ipe_cia_aberta_2024.zip",
            force=True,
        )


def test_cvm_and_cotahist_delegate_to_the_shared_downloader(tmp_path):
    from b3_pipeline import cvm_downloader, downloader

    with patch("b3_pipeline.cvm_downloader.config") as mock_cfg, \
         patch.object(downloader, "download_file") as mock_dl:
        mock_cfg.CVM_DATA_DIR = tmp_path
        mock_cfg.CVM_DFP_BASE_URL = "https://example.com/"
        cvm_downloader.download_dfp_file(2024)
        assert mock_dl.call_args.args[0] == "https://example.com/dfp_cia_aberta_2024.zip"

    with patch("b3_pipeline.downloader.config") as mock_cfg, \
         patch.object(downloader, "download_file") as mock_dl:
        mock_cfg.DATA_DIR = tmp_path
        mock_cfg.BASE_COTAHIST_URL = "https://b3.example.com/COTAHIST_A{year}.ZIP"
        downloader.download_annual_file(2024)
        mock_dl.assert_called_once_with(
            "https://b3.example.com/COTAHIST_A2024.ZIP",
            tmp_path / "COTAHIST_A2024.ZIP",
            force=False,
        )
