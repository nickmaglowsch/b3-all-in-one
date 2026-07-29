"""
Download CVM bulk data files (DFP, ITR, FRE, FCA) from CVM's open data portal.

Each is the same yearly-ZIP fetch with a different base URL, so the streaming,
existence check and partial-file cleanup all live in downloader.download_file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import config, downloader


def _yearly_zip(base_url: str, stem: str, year: int, force: bool) -> Optional[Path]:
    filename = f"{stem}_cia_aberta_{year}.zip"
    return downloader.download_file(
        f"{base_url}{filename}", config.CVM_DATA_DIR / filename, force=force
    )


def download_dfp_file(year: int, force: bool = False) -> Optional[Path]:
    """Download a DFP annual ZIP file for the given year."""
    return _yearly_zip(config.CVM_DFP_BASE_URL, "dfp", year, force)


def download_itr_file(year: int, force: bool = False) -> Optional[Path]:
    """Download an ITR quarterly ZIP file for the given year."""
    return _yearly_zip(config.CVM_ITR_BASE_URL, "itr", year, force)


def download_fre_file(year: int, force: bool = False) -> Optional[Path]:
    """Download a FRE ZIP file for the given year."""
    return _yearly_zip(config.CVM_FRE_BASE_URL, "fre", year, force)


def download_fca_file(year: int, force: bool = False) -> Optional[Path]:
    """Download an FCA (Formulário Cadastral) ZIP file for the given year."""
    return _yearly_zip(config.CVM_FCA_BASE_URL, "fca", year, force)
